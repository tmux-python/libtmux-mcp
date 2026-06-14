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
    ToolErrorResultMiddleware,
    install_fastmcp_validation_log_filter,
)
from libtmux_mcp.tools.buffer_tools import _MCP_BUFFER_PREFIX

logger = logging.getLogger(__name__)
install_fastmcp_validation_log_filter()

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
# Tests assert on substrings of ``_BASE_INSTRUCTIONS``, so the join
# shape (segment count, ``"\n\n"`` separator) must stay stable even as
# individual instruction strings evolve.
# ---------------------------------------------------------------------------

_INSTR_HIERARCHY = (
    "libtmux MCP server for tmux. "
    "tmux hierarchy: Server > Session > Window > Pane. "
    "Prefer pane_id (e.g. '%1') for targeting. "
    "Targeted tmux tools accept socket_name (defaults to LIBTMUX_SOCKET); "
    "list_servers discovers sockets via TMUX_TMPDIR plus extra_socket_paths."
)

#: Activation rule. Names positive triggers and explicit anti-triggers
#: so bare 'pane'/'window'/'session' default to tmux but the server
#: stays out of the way for browser/editor/GUI/Jupyter contexts.
_INSTR_SCOPE = (
    "TRIGGERS: invoke for tmux objects (panes, windows, sessions). "
    "Bare 'pane', 'split', 'this terminal', 'send keys', 'scrollback', "
    "'copy mode' default to tmux. IDs '%' (pane), '@' (window), "
    "'$' (session) are unambiguous.\n"
    "ANTI-TRIGGERS: do NOT invoke for browser windows/tabs, editor panes "
    "(VS Code, Cursor, Neovim splits), GUI windows (i3, sway, Hyprland), "
    "Jupyter cells, login/HTTP sessions.\n"
    "When ambiguous on bare 'window'/'session', ask one clarifying question."
)

_INSTR_METADATA_VS_CONTENT = (
    "metadata vs content: list_windows/list_panes/list_sessions search "
    "metadata only. Use search_panes/capture_since/capture_pane for terminal "
    "text — what panes 'contain', 'mention', 'show'."
)

_INSTR_READ_TOOLS = (
    "Prefer snapshot_pane over capture_pane + get_pane_info; capture_since "
    "for repeated observation/tailing; display_message for tmux formats."
)

_INSTR_WAIT_NOT_POLL = (
    "WAIT, DON'T POLL: run_command for authored commands needing "
    "status; wait_for_channel for custom tmux wait-for; capture_since "
    "for tailing; wait_for_text/wait_for_content_change for output you "
    "don't author; send_keys_batch for raw input."
)

#: Gap-explainer: write-hook tools are intentionally absent. See module
#: comment above for when to add another ``_GAP`` segment vs. push the
#: explanation into a tool description.
_INSTR_HOOKS_GAP = (
    "HOOKS ARE READ-ONLY: inspect via show_hooks / show_hook. "
    "Write-hooks survive process death; keep them in your tmux config file, "
    "not a transient MCP session."
)

#: Gap-explainer: ``list_buffers`` is intentionally absent because tmux
#: buffers can include OS clipboard history. See module comment above.
_INSTR_BUFFERS_GAP = (
    "BUFFERS: load_buffer stages, paste_buffer delivers, delete_buffer "
    "removes. Track via the BufferRef returned from load_buffer — no "
    "list_buffers tool because tmux buffers may include OS clipboard history."
)

_BASE_INSTRUCTIONS = "\n\n".join(
    (
        _INSTR_HIERARCHY,
        _INSTR_SCOPE,
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
        f"\n\nSafety level: {safety_level} "
        "(values: readonly, mutating, destructive). "
        "Set LIBTMUX_SAFETY; off-tier tools are hidden."
    )

    # Tier-conditioned discoverability hint. False-positive activation is
    # cheap on readonly (worst case: an extra list_panes call) and
    # expensive on mutating/destructive (where kill_* is one mis-routed
    # query away). Reuse the existing safety axis instead of shipping a
    # separate LIBTMUX_DISCOVERABILITY knob.
    if safety_level == TAG_READONLY:
        parts.append(
            "\n\nReadonly mode: if uncertain, prefer "
            "one read-only probe (snapshot_pane, list_panes, search_panes)."
        )

    # Agent tmux context
    tmux_pane = os.environ.get("TMUX_PANE")
    if tmux_pane:
        # Parse TMUX env: "/tmp/tmux-1000/default,48188,10"
        tmux_env = os.environ.get("TMUX", "")
        env_parts = tmux_env.split(",") if tmux_env else []
        socket_path = env_parts[0] if env_parts else None
        socket_name = socket_path.rsplit("/", 1)[-1] if socket_path else None

        context = f"\n\nAgent context: this MCP runs inside tmux pane {tmux_pane}"
        if socket_name:
            context += f" (socket {socket_name})"
        context += (
            ". Tool results mark is_caller=true; filter list_panes for it to answer "
            "'which pane am I in?' (no whoami tool)."
        )
        parts.append(context)

    return "".join(parts)


def _resolve_safety_level(value: str | None) -> str:
    """Return the effective safety level for a ``LIBTMUX_SAFETY`` value."""
    if value is None:
        return TAG_MUTATING
    if value in VALID_SAFETY_LEVELS:
        return value
    logger.warning(
        "invalid LIBTMUX_SAFETY=%r, falling back to %s",
        value,
        TAG_READONLY,
    )
    return TAG_READONLY


_safety_level = _resolve_safety_level(os.environ.get("LIBTMUX_SAFETY"))

#: Tools covered by the tail-preserving response limiter. Only tools
#: whose output is terminal scrollback benefit from this backstop;
#: structured responses from list/get tools stay under the cap naturally.
_RESPONSE_LIMITED_TOOLS = [
    "capture_pane",
    "capture_since",
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
                server.delete_buffer(buffer_name=name)
            except Exception as err:
                logger.debug("buffer GC: delete-buffer %s failed: %s", name, err)


mcp = FastMCP(
    name="tmux",
    version=__version__,
    instructions=_build_instructions(safety_level=_safety_level),
    website_url="https://libtmux-mcp.git-pull.com/",
    lifespan=_lifespan,
    # Middleware runs outermost-first. Order rationale:
    #   1. TimingMiddleware — neutral observer; start clock as early
    #      as possible so timing captures middleware cost too.
    #   2. TailPreservingResponseLimitingMiddleware — bounds the final
    #      tool result on the way back out. Tool errors may already be
    #      ToolResult(is_error=True) here, so truncation preserves that
    #      flag instead of turning expected failures into schema errors.
    #   3. ToolErrorResultMiddleware — converts tool-call failures to
    #      rich ToolResult(is_error=True) results and transforms
    #      resource errors to MCP code -32002. Must stay OUTSIDE the
    #      audit + retry + safety trio: all three depend on exception
    #      semantics (audit catches to record outcome=error, retry
    #      matches LibTmuxException via __cause__, and safety's tier
    #      denials must propagate as exceptions for audit to record
    #      them), so converting the exception to a result any deeper
    #      would silently break all three.
    #   4. AuditMiddleware — outside SafetyMiddleware so tier-denial
    #      events (which raise ExpectedToolError before call_next inside
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
        ToolErrorResultMiddleware(transform_errors=True),
        AuditMiddleware(),
        ReadonlyRetryMiddleware(),
        SafetyMiddleware(max_tier=_safety_level),
    ],
    on_duplicate="error",
)


_mcp_registered = False
_mcp_visibility_configured = False


def _register_all() -> None:
    """Register all tools, resources, and prompts with the MCP server."""
    global _mcp_registered
    if _mcp_registered:
        return

    from libtmux_mcp.prompts import register_prompts
    from libtmux_mcp.resources import register_resources
    from libtmux_mcp.tools import register_tools

    register_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)
    _mcp_registered = True


def _enable_allowed_tools() -> None:
    """Apply the native FastMCP visibility gate for the active safety tier."""
    global _mcp_visibility_configured
    if _mcp_visibility_configured:
        return

    # Use FastMCP's native visibility system as primary gate,
    # with the SafetyMiddleware as a secondary layer for clear error messages.
    allowed_tags = {TAG_READONLY}
    if _safety_level in {TAG_MUTATING, TAG_DESTRUCTIVE}:
        allowed_tags.add(TAG_MUTATING)
    if _safety_level == TAG_DESTRUCTIVE:
        allowed_tags.add(TAG_DESTRUCTIVE)
    mcp.disable(components={"tool"})
    mcp.enable(tags=allowed_tags, components={"tool"})
    _mcp_visibility_configured = True


def build_mcp_server() -> FastMCP:
    """Build and return the registered production FastMCP server.

    This factory is used by ``fastmcp.json`` so FastMCP's CLI can inspect
    or run the same populated server that the ``libtmux-mcp`` console
    script starts.
    """
    _register_all()
    _enable_allowed_tools()
    return mcp


def run_server() -> None:
    """Run the MCP server."""
    server = build_mcp_server()
    server.run(transport="stdio")
