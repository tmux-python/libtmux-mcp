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
from libtmux_mcp._history import (
    _configure_history_defaults,
    _resolve_suppress_history,
)
from libtmux_mcp._utils import (
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    VALID_SAFETY_LEVELS,
    _server_cache,
)
from libtmux_mcp._wait_policy import (
    WAIT_MAX_SECONDS_ENV,
    _configure_wait_ceiling,
    _resolve_wait_max_seconds,
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

#: Cache key for :data:`_server_cache`: ``(socket_name, socket_path,
#: tmux_bin)``.
_ServerCacheKey: t.TypeAlias = tuple[str | None, str | None, str | None]

# ---------------------------------------------------------------------------
# _BASE_INSTRUCTIONS — positive guidance plus gap-explainers for tools an
# agent might expect to exist. Before adding a ``_GAP`` segment, push the
# explanation into the relevant tool's docstring, where the agent meets it
# at call time; a server-level segment is for a server-shaped gap, such as
# a whole tool family. Tests assert on substrings, so the join shape
# (segment count, ``"\n\n"`` separator) must stay stable.
# ---------------------------------------------------------------------------

_INSTR_HIERARCHY = (
    "libtmux MCP server for tmux. "
    "tmux hierarchy: Server > Session > Window > Pane. "
    "Target with pane_id (e.g. '%1'); input tools require one. "
    "Targeted tmux tools accept socket_name (defaults to LIBTMUX_SOCKET); "
    "list_servers discovers sockets via TMUX_TMPDIR plus extra_socket_paths."
)

#: Activation rule: bare 'pane'/'window'/'session' default to tmux, with
#: anti-triggers keeping browser, editor, GUI and Jupyter contexts clear.
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
    "for tailing; wait_for_text for output you don't author "
    "(patterns=null=any output; a stop hit returns at once); "
    "send_keys_batch for raw input."
)

#: Gap-explainer: write-hook tools are intentionally absent.
_INSTR_HOOKS_GAP = (
    "HOOKS ARE READ-ONLY: inspect via show_hooks/show_hook. "
    "Write hooks survive process death; keep them in your tmux config file."
)

#: Gap-explainer: ``list_buffers`` is intentionally absent because tmux
#: buffers can include OS clipboard history. See module comment above.
_INSTR_BUFFERS_GAP = (
    "BUFFERS: load_buffer stages, paste_buffer delivers, delete_buffer "
    "removes by BufferRef. No list_buffers: they may hold clipboard history."
)

_BASE_INSTRUCTIONS = (
    f"{_INSTR_HIERARCHY}\n\n"
    f"{_INSTR_SCOPE}\n\n"
    f"{_INSTR_METADATA_VS_CONTENT}\n\n"
    f"{_INSTR_READ_TOOLS}\n\n"
    f"{_INSTR_WAIT_NOT_POLL}\n\n"
    f"{_INSTR_HOOKS_GAP}\n\n"
    f"{_INSTR_BUFFERS_GAP}"
)

_INSTRUCTIONS_MAX_BYTES = 2048


def _build_instructions(
    safety_level: str = TAG_MUTATING,
    suppress_history: bool = True,
) -> str:
    """Build server instructions with agent context and safety level.

    When the MCP server process runs inside a tmux pane, ``TMUX_PANE`` and
    ``TMUX`` environment variables are available. This function appends that
    context so the LLM knows which pane is its own without extra tool calls.

    Parameters
    ----------
    safety_level : str
        Active safety tier (readonly, mutating, or destructive).
    suppress_history : bool
        Effective MCP default for semantic shell-command suppression.

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
    history_default = "true" if suppress_history else "false"
    parts.append(
        f"\n\nsuppress_history={history_default}: run_command inherits; "
        "raw send/batch/paste and spawn do not."
    )

    # Tier-conditioned discoverability hint: a false positive costs an
    # extra list_panes on readonly, but on mutating/destructive kill_* is
    # one mis-routed query away.
    if safety_level == TAG_READONLY:
        parts.append(
            "\n\nReadonly mode: probe snapshot_pane/list_panes/search_panes if unsure."
        )

    instructions = "".join(parts)
    if len(instructions.encode("utf-8")) > _INSTRUCTIONS_MAX_BYTES:
        msg = "required server instructions exceed the 2048-byte MCP budget"
        raise RuntimeError(msg)

    # Agent tmux context is optional: prefer the complete form, then drop
    # the untrusted socket name, then the context entirely. Never
    # byte-slice -- a UTF-8 character may split across bytes.
    tmux_pane = os.environ.get("TMUX_PANE")
    if tmux_pane:
        # Parse TMUX env: "/tmp/tmux-1000/default,48188,10"
        tmux_env = os.environ.get("TMUX", "")
        env_parts = tmux_env.split(",") if tmux_env else []
        socket_path = env_parts[0] if env_parts else None
        socket_name = socket_path.rsplit("/", 1)[-1] if socket_path else None

        context_start = f"\n\nAgent context: this MCP runs inside tmux pane {tmux_pane}"
        context = context_start
        if socket_name:
            context += f" (socket {socket_name})"
        context += (
            ". Tool results mark is_caller=true; filter list_panes for it to answer "
            "'which pane am I in?' (no whoami tool)."
        )
        pane_context = (
            f"{context_start}. Tool results mark is_caller=true; filter list_panes "
            "for it to answer 'which pane am I in?' (no whoami tool)."
        )
        # Only the socket name is optional. Dropping it costs an agent
        # nothing it cannot re-derive, so that degradation stays silent.
        for candidate in (context, pane_context):
            combined = instructions + candidate
            if len(combined.encode("utf-8")) <= _INSTRUCTIONS_MAX_BYTES:
                return combined

        # The is_caller workflow no longer fits. A nominal pane id
        # separates the two causes: if it fits with a realistic id, only
        # oversized TMUX_PANE/TMUX pushed us over, so degrade rather than
        # refuse to start over hostile runtime data. If it does not, our
        # own _INSTR_* segments grew -- a build-time bug, and silently
        # degrading hides it from the total-size assertions.
        nominal_context = (
            "\n\nAgent context: this MCP runs inside tmux pane %000"
            ". Tool results mark is_caller=true; filter list_panes "
            "for it to answer 'which pane am I in?' (no whoami tool)."
        )
        if len((instructions + nominal_context).encode("utf-8")) > (
            _INSTRUCTIONS_MAX_BYTES
        ):
            msg = (
                "server instructions leave no room for the is_caller agent "
                f"context within the {_INSTRUCTIONS_MAX_BYTES}-byte MCP budget "
                f"(need {len((instructions + nominal_context).encode('utf-8'))} "
                "bytes); shorten an _INSTR_* segment rather than letting agent "
                "context be dropped"
            )
            raise RuntimeError(msg)

    return instructions


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
_suppress_history = _resolve_suppress_history(
    os.environ.get("LIBTMUX_SUPPRESS_HISTORY")
)
_wait_max_seconds = _resolve_wait_max_seconds(os.environ.get(WAIT_MAX_SECONDS_ENV))

#: Tools whose output is terminal scrollback, so they need the
#: tail-preserving limiter; structured responses stay under the cap.
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
    instructions=_build_instructions(
        safety_level=_safety_level,
        suppress_history=_suppress_history,
    ),
    website_url="https://libtmux-mcp.git-pull.com/",
    lifespan=_lifespan,
    # Middleware runs outermost-first, and positions 2-6 are load-bearing:
    #   1. Timing — a neutral observer, outermost so the clock covers
    #      middleware cost too.
    #   2. TailPreservingResponseLimiting — truncation preserves an
    #      is_error result instead of making it a schema error.
    #   3. ToolErrorResult — must stay OUTSIDE audit/retry/safety: all
    #      three read exception semantics, so converting to a result any
    #      deeper breaks them.
    #   4. Audit — outside Safety, or tier denials bypass the audit log.
    #   5. ReadonlyRetry — inside Audit so each retry is audited, outside
    #      Safety so a denied tool never reaches retry.
    #   6. Safety — innermost, fail-closed.
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
    _configure_history_defaults(mcp, _suppress_history)
    # Server owns env resolution; tool modules never import server globals.
    _configure_wait_ceiling(_wait_max_seconds)
    register_resources(mcp)
    register_prompts(mcp)
    _mcp_registered = True


def _enable_allowed_tools() -> None:
    """Apply the native FastMCP visibility gate for the active safety tier."""
    global _mcp_visibility_configured
    if _mcp_visibility_configured:
        return

    # The ENFORCEMENT gate: it holds even for a call that skips the
    # middleware chain (``call_tool`` accepts ``run_middleware=False``).
    # ``SafetyMiddleware`` is the EXPLANATION gate -- disabling makes
    # ``get_tool`` answer None, so FastMCP would otherwise report a gated
    # tool as ``Unknown tool``.
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
