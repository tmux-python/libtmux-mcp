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
# _BASE_INSTRUCTIONS — composed from named segments.
#
# The string handed to FastMCP grew organically from "what does this server
# do?" toward a hybrid of positive guidance (HIERARCHY, READ_TOOLS,
# WAIT_NOT_POLL) and *gap-explainers* (HOOKS_GAP, BUFFERS_GAP) that document
# why a tool the agent might expect is absent. Splitting into named
# constants keeps additions deliberate: when a new ``_GAP`` segment feels
# tempting, prefer first to push the explanation into the relevant tool's
# docstring/description (where the agent encounters it at call time) and
# only fall back to a server-level segment when the gap is *server-shaped*
# (e.g. an entire tool family is intentionally missing).
#
# Output text is byte-identical to the previous monolith; tests assert on
# substrings of ``_BASE_INSTRUCTIONS``, so keeping the join shape stable
# matters.
# ---------------------------------------------------------------------------

_INSTR_HIERARCHY = (
    "libtmux MCP server for programmatic tmux control. "
    "tmux hierarchy: Server > Session > Window > Pane. "
    "Use pane_id (e.g. '%1') as the preferred targeting method - "
    "it is globally unique within a tmux server. "
    "Use send_keys to execute commands and capture_pane to read output. "
    "Targeted tmux tools accept an optional socket_name parameter "
    "(defaults to LIBTMUX_SOCKET env var); list_servers discovers "
    "sockets via TMUX_TMPDIR plus optional extra_socket_paths instead."
)

_INSTR_METADATA_VS_CONTENT = (
    "IMPORTANT — metadata vs content: list_windows, list_panes, and "
    "list_sessions only search metadata (names, IDs, current command). "
    "To find text that is actually visible in terminals — when users ask "
    "what panes 'contain', 'mention', 'show', or 'have' — use "
    "search_panes to search across all pane contents, or list_panes + "
    "capture_pane on each pane for manual inspection."
)

_INSTR_READ_TOOLS = (
    "READ TOOLS TO PREFER: snapshot_pane returns pane content plus "
    "cursor position, mode, and scroll state in one call — use it "
    "instead of capture_pane + get_pane_info when you need context. "
    "display_message evaluates a tmux format string (e.g. "
    "'#{pane_current_command}', '#{session_name}') against a target "
    "and returns the expanded value — cheaper than parsing captured "
    "output. (The tool is named after the tmux 'display-message -p' "
    "verb it wraps; its MCP title is 'Evaluate tmux Format String'.)"
)

_INSTR_WAIT_NOT_POLL = (
    "WAIT, DON'T POLL: for 'run command, wait for output' workflows "
    "use wait_for_text (matches text/regex on a pane) or "
    "wait_for_content_change (waits for any change). These block "
    "server-side until the condition is met or the timeout expires, "
    "which is dramatically cheaper in agent turns than capture_pane "
    "in a retry loop."
)

#: Gap-explainer: write-hook tools are intentionally absent. See module
#: comment above for when to add another ``_GAP`` segment vs. push the
#: explanation into a tool description.
_INSTR_HOOKS_GAP = (
    "HOOKS ARE READ-ONLY: inspect via show_hooks / show_hook. Write-hook "
    "tools are intentionally not exposed — tmux hooks survive process "
    "death, so they belong in your tmux config file, not a transient "
    "MCP session."
)

#: Gap-explainer: ``list_buffers`` is intentionally absent because tmux
#: buffers can include OS clipboard history. See module comment above.
_INSTR_BUFFERS_GAP = (
    "BUFFERS: load_buffer stages content, paste_buffer delivers it into "
    "a pane, delete_buffer removes the staged buffer. Track owned "
    "buffers via the BufferRef returned from load_buffer — there is no "
    "list_buffers tool because tmux buffers may include OS clipboard "
    "history (passwords, private snippets)."
)

_BASE_INSTRUCTIONS = "\n\n".join(
    (
        _INSTR_HIERARCHY,
        _INSTR_METADATA_VS_CONTENT,
        _INSTR_READ_TOOLS,
        _INSTR_WAIT_NOT_POLL,
        _INSTR_HOOKS_GAP,
        _INSTR_BUFFERS_GAP,
    )
)


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
