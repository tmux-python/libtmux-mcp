"""FastMCP server instance for libtmux.

Creates and configures the MCP server with all tools and resources.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import typing as t

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware

if t.TYPE_CHECKING:
    from libtmux.server import Server

from libtmux_mcp.__about__ import __version__
from libtmux_mcp._utils import (
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    VALID_SAFETY_LEVELS,
    _server_cache,
)
from libtmux_mcp.middleware import (
    DEFAULT_RESPONSE_LIMIT_BYTES,
    AuditMiddleware,
    ReadonlyRetryMiddleware,
    SafetyMiddleware,
    TailPreservingResponseLimitingMiddleware,
)
from libtmux_mcp.tools.buffer_tools import _MCP_BUFFER_PREFIX

logger = logging.getLogger(__name__)

#: Cache-key shape used by :data:`_server_cache` and the GC helper.
#: ``(socket_name, socket_path, tmux_bin)`` — see
#: :func:`libtmux_mcp._utils._get_server`.
_ServerCacheKey: t.TypeAlias = tuple[str | None, str | None, str | None]

# ---------------------------------------------------------------------------
# _BASE_INSTRUCTIONS — slim "three handles" card.
#
# The card answers two questions the agent has at session start:
# (1) what is this server, and (2) where do I look for the rest? It points
# at tools, resources, and prompts — and that's it. Tool-specific rules
# (which tool to prefer, what's intentionally not exposed and why) live in
# the relevant tool's docstring or ``description=`` override, where the
# agent reads them on every ``list_tools`` call instead of parsing them
# out of a one-shot prompt that has long since rolled out of context.
#
# When in doubt about adding text here, ask: is this rule cross-cutting
# (about the server as a whole) or tool-specific (about when to call X
# vs Y)? Cross-cutting belongs in the card; tool-specific belongs in the
# tool description. ``test_card_length_budget`` enforces a soft 200-word
# ceiling against creeping re-bloat.
# ---------------------------------------------------------------------------

_INSTR_CARD = (
    "libtmux MCP server: programmatic tmux control. tmux hierarchy is "
    "Server > Session > Window > Pane; every pane has a globally unique "
    "pane_id like %1 — prefer it over name/index for targeting. Targeted "
    "tools accept an optional socket_name (defaults to LIBTMUX_SOCKET); "
    "list_servers discovers sockets via TMUX_TMPDIR / extra_socket_paths "
    "and is the documented socket_name exception."
)

_INSTR_HANDLES = (
    "Three handles cover everything the agent needs:\n"
    "- Tools — call list_tools; per-tool descriptions tell you which to "
    "prefer (e.g. snapshot_pane over capture_pane + get_pane_info, "
    "wait_for_text over capture_pane in a retry loop, search_panes over "
    'list_panes when the user says "panes that contain X").\n'
    "- Resources (tmux://) — browseable hierarchy plus reference cards "
    "(format strings).\n"
    "- Prompts — packaged workflows: run_and_wait, diagnose_failing_pane, "
    "build_dev_workspace, interrupt_gracefully."
)

_BASE_INSTRUCTIONS = "\n\n".join((_INSTR_CARD, _INSTR_HANDLES))


def _build_instructions(safety_level: str = TAG_MUTATING) -> str:
    """Build server instructions with agent context and safety level.

    When the MCP server process runs inside a tmux pane, ``TMUX_PANE`` and
    ``TMUX`` environment variables are available. This function appends that
    context so the LLM knows which pane is its own without extra tool calls.

    Parameters
    ----------
    safety_level : str
        Active safety tier (readonly, mutating, or destructive).

    Returns
    -------
    str
        Server instructions string, optionally with agent tmux context.
    """
    parts: list[str] = [_BASE_INSTRUCTIONS]

    # Safety tier context
    parts.append(
        f"\n\nSafety level: {safety_level}. "
        "Available tiers: 'readonly' (read operations only), "
        "'mutating' (default, read + write + send_keys), "
        "'destructive' (all operations including kill commands). "
        "Set via LIBTMUX_SAFETY env var. "
        "Tools outside the active tier are hidden and will not appear in "
        "tool listings."
    )

    # Agent tmux context
    tmux_pane = os.environ.get("TMUX_PANE")
    if tmux_pane:
        # Parse TMUX env: "/tmp/tmux-1000/default,48188,10"
        tmux_env = os.environ.get("TMUX", "")
        env_parts = tmux_env.split(",") if tmux_env else []
        socket_path = env_parts[0] if env_parts else None
        socket_name = socket_path.rsplit("/", 1)[-1] if socket_path else None

        context = (
            f"\n\nAgent context: This MCP server is running inside "
            f"tmux pane {tmux_pane}"
        )
        if socket_name:
            context += f" (socket: {socket_name})"
        context += (
            ". Tool results annotate the caller's own pane with "
            "is_caller=true. Use this to distinguish your own pane from "
            "others. To answer 'which pane/window/session am I in?' call "
            "list_panes (or snapshot_pane) and filter for is_caller=true — "
            "your pane is identified above. No dedicated whoami tool exists."
        )
        parts.append(context)

    return "".join(parts)


_safety_level = os.environ.get("LIBTMUX_SAFETY", TAG_MUTATING)
if _safety_level not in VALID_SAFETY_LEVELS:
    logger.warning(
        "invalid LIBTMUX_SAFETY=%r, falling back to %s",
        _safety_level,
        TAG_MUTATING,
    )
    _safety_level = TAG_MUTATING

#: Tools covered by the tail-preserving response limiter. Only tools
#: whose output is terminal scrollback benefit from this backstop;
#: structured responses from list/get tools stay under the cap naturally.
_RESPONSE_LIMITED_TOOLS = [
    "capture_pane",
    "search_panes",
    "snapshot_pane",
    "show_buffer",
]


@contextlib.asynccontextmanager
async def _lifespan(_app: FastMCP) -> t.AsyncIterator[None]:
    """FastMCP lifespan: fail-fast startup + deterministic cache cleanup.

    Startup
    -------
    Verifies that a ``tmux`` binary is on ``PATH``. Without this
    probe, tools fail at first call with a generic ``TmuxCommandNotFound``
    deep inside libtmux. Failing at server start instead surfaces a
    clear cold-start error before any tool traffic arrives.

    Shutdown
    --------
    Clears the process-wide :data:`_server_cache` so repeated test runs
    don't share stale Server references and HTTP-transport reload
    cycles start clean. Also best-effort GC's any leftover
    ``libtmux_mcp_*`` paste buffers on every cached server — agents
    are supposed to ``delete_buffer`` after use, but an interrupted
    call chain can leak. Note: FastMCP lifespan teardown runs on
    SIGTERM / SIGINT only; ``kill -9`` and OOM bypass it, so this path
    must not be relied on for any invariant that must survive a hard
    crash (see the hook_tools module docstring for why write-hooks
    are explicitly NOT gated on lifespan cleanup).
    """
    if shutil.which("tmux") is None:
        msg = "tmux binary not found on PATH"
        raise RuntimeError(msg)
    try:
        yield
    finally:
        _gc_mcp_buffers(_server_cache)
        _server_cache.clear()


def _gc_mcp_buffers(cache: t.Mapping[_ServerCacheKey, Server]) -> None:
    """Best-effort delete of leaked ``libtmux_mcp_*`` paste buffers.

    Iterates every cached tmux Server, lists buffer names, and deletes
    anything matching the MCP prefix. Never raises: tmux may be
    unreachable, buffers may vanish mid-scan, and none of that should
    block lifespan shutdown. Logs at debug level so operators can
    still surface leaks via verbose logging.
    """
    for server in cache.values():
        try:
            result = server.cmd("list-buffers", "-F", "#{buffer_name}")
        except Exception as err:
            logger.debug("buffer GC: list-buffers failed: %s", err)
            continue
        for name in result.stdout:
            if not name.startswith(_MCP_BUFFER_PREFIX):
                continue
            try:
                server.cmd("delete-buffer", "-b", name)
            except Exception as err:
                logger.debug("buffer GC: delete-buffer %s failed: %s", name, err)


mcp = FastMCP(
    name="libtmux",
    version=__version__,
    instructions=_build_instructions(safety_level=_safety_level),
    lifespan=_lifespan,
    # Middleware runs outermost-first. Order rationale:
    #   1. TimingMiddleware — neutral observer; start clock as early
    #      as possible so timing captures middleware cost too.
    #   2. TailPreservingResponseLimitingMiddleware — bound the
    #      response *before* ErrorHandlingMiddleware can transform
    #      exceptions; keeps the size cap independent of error path.
    #   3. ErrorHandlingMiddleware — transforms resource errors to
    #      MCP code -32002; sits inside so it wraps the audit + safety
    #      pair.
    #   4. AuditMiddleware — outside SafetyMiddleware so tier-denial
    #      events (which raise ToolError before call_next inside
    #      Safety) are still logged with outcome=error. Without this
    #      ordering, denied access attempts would silently bypass the
    #      audit log — a security-observability gap.
    #   5. ReadonlyRetryMiddleware — inside Audit so retries are
    #      audited once each, outside Safety so tier-denied tools
    #      never reach retry. Only readonly tools are retried;
    #      mutating/destructive tools pass straight through.
    #   6. SafetyMiddleware — innermost gate (fail-closed). Denials
    #      never reach the tool, but the audit record above captures
    #      them for forensic review.
    middleware=[
        TimingMiddleware(),
        TailPreservingResponseLimitingMiddleware(
            max_size=DEFAULT_RESPONSE_LIMIT_BYTES,
            tools=_RESPONSE_LIMITED_TOOLS,
        ),
        ErrorHandlingMiddleware(transform_errors=True),
        AuditMiddleware(),
        ReadonlyRetryMiddleware(),
        SafetyMiddleware(max_tier=_safety_level),
    ],
    on_duplicate="error",
)


def _register_all() -> None:
    """Register all tools, resources, and prompts with the MCP server."""
    from libtmux_mcp.prompts import register_prompts
    from libtmux_mcp.resources import register_resources
    from libtmux_mcp.tools import register_tools

    register_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)


def run_server() -> None:
    """Run the MCP server."""
    _register_all()

    # Use FastMCP's native visibility system as primary gate,
    # with the SafetyMiddleware as a secondary layer for clear error messages.
    allowed_tags = {TAG_READONLY}
    if _safety_level in {TAG_MUTATING, TAG_DESTRUCTIVE}:
        allowed_tags.add(TAG_MUTATING)
    if _safety_level == TAG_DESTRUCTIVE:
        allowed_tags.add(TAG_DESTRUCTIVE)
    mcp.enable(tags=allowed_tags, only=True)

    mcp.run()
