"""FastMCP server instance for libtmux.

Creates and configures the MCP server with all tools and resources.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import typing as t

from fastmcp import FastMCP
from fastmcp.server.middleware.timing import TimingMiddleware

from libtmux_mcp.__about__ import __version__
from libtmux_mcp._history import (
    _configure_history_defaults,
    _resolve_suppress_history,
)
from libtmux_mcp._utils import (
    TOOLSET_EXECUTE,
    TOOLSET_INSPECT,
    TOOLSET_MANAGE,
    VALID_TOOLSETS,
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
    TailPreservingResponseLimitingMiddleware,
    ToolErrorResultMiddleware,
    ToolsetMiddleware,
    install_fastmcp_validation_log_filter,
)

install_fastmcp_validation_log_filter()

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
    "for repeated observation/tailing; display_message for tmux variables."
)

_INSTR_WAIT_NOT_POLL = (
    "WAIT, DON'T POLL: run_command for authored commands needing "
    "status; wait_for_channel for custom tmux wait-for; capture_since "
    "for tailing; wait_for_text for output you don't author "
    "(patterns=null=any output; stop=[] bails); "
    "send_keys_batch for raw input."
)

#: Gap-explainer: write-hook tools are intentionally absent. See module
#: comment above for when to add another ``_GAP`` segment vs. push the
#: explanation into a tool description.
_INSTR_HOOKS_GAP = (
    "NO DEDICATED HOOK-WRITE TOOLS: use show_hooks/show_hook. "
    "Write hooks survive process death; keep them in your tmux config file."
)

#: Gap-explainer: ``list_buffers`` is intentionally absent because tmux
#: buffers can include OS clipboard history. See module comment above.
_INSTR_BUFFERS_GAP = (
    "BUFFERS: load_buffer stages, paste_buffer delivers, delete_buffer "
    "removes via returned BufferRef. No list_buffers: tmux buffers may include "
    "clipboard history."
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

#: Enabled when ``LIBTMUX_TOOLSETS`` is unset. ``teardown`` is not in it:
#: this server still reaches whichever tmux server the environment points
#: at, so deletion stays something an operator asks for by name.
DEFAULT_TOOLSETS: frozenset[str] = frozenset(
    {TOOLSET_INSPECT, TOOLSET_MANAGE, TOOLSET_EXECUTE}
)


def _build_instructions(
    toolsets: frozenset[str] = DEFAULT_TOOLSETS,
    suppress_history: bool = True,
) -> str:
    """Build server instructions with agent context and toolsets.

    When the MCP server process runs inside a tmux pane, ``TMUX_PANE`` and
    ``TMUX`` environment variables are available. This function appends that
    context so the LLM knows which pane is its own without extra tool calls.

    Parameters
    ----------
    toolsets : frozenset of str
        Enabled toolsets.
    suppress_history : bool
        Effective MCP default for semantic shell-command suppression.

    Returns
    -------
    str
        Server instructions string, optionally with agent tmux context.
    """
    parts: list[str] = [_BASE_INSTRUCTIONS]

    # Toolset context
    parts.append(
        "\n\nToolsets: "
        + (", ".join(sorted(toolsets)) or "(none)")
        + f" (of {', '.join(VALID_TOOLSETS)}), set by LIBTMUX_TOOLSETS. "
        "Hiding one shapes this list, not what a pane can run."
    )
    history_default = "true" if suppress_history else "false"
    parts.append(
        f"\n\nsuppress_history={history_default}: run_command inherits; "
        "raw send/batch/paste and spawn do not."
    )

    # Only when nothing but inspect is enabled: a wrong guess costs one
    # extra capture, where the same nudge on a surface holding kill_* or
    # send_keys could cost a pane. Keyed on the enabled toolsets rather
    # than a separate discoverability variable.
    if toolsets == frozenset({TOOLSET_INSPECT}):
        parts.append("\n\nProbe snapshot_pane/list_panes/search_panes if unsure.")

    instructions = "".join(parts)
    if len(instructions.encode("utf-8")) > _INSTRUCTIONS_MAX_BYTES:
        msg = "required server instructions exceed the 2048-byte MCP budget"
        raise RuntimeError(msg)

    # Agent tmux context is optional. Prefer the complete form, then discard
    # the untrusted socket name and explanatory workflow before omitting the
    # context entirely. Never byte-slice text because UTF-8 characters may be
    # split across bytes.
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

        # Past this point the is_caller workflow — the only place an
        # agent learns how to answer "which pane am I in?" — cannot fit.
        # There are two very different reasons for that, and they need
        # opposite handling:
        #
        #   (a) our own _INSTR_* segments grew until the workflow no
        #       longer fits alongside them. That is a build-time bug in
        #       this file, and silently dropping the workflow hides it:
        #       the budget assertions only check the total size, and
        #       the degraded form still contains "Agent context", so
        #       nothing fails. Raise and make the author shorten a
        #       segment.
        #
        #   (b) TMUX_PANE / TMUX are pathologically large. That is
        #       runtime data we do not control, and refusing to start
        #       over a hostile environment variable would be a denial
        #       of service. Degrade, as before.
        #
        # A nominal pane id discriminates: if the workflow fits with a
        # realistic id, only the oversized runtime data pushed us over.
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


def _resolve_toolsets(value: str | None) -> frozenset[str]:
    """Return the enabled toolsets for a ``LIBTMUX_TOOLSETS`` value.

    Parameters
    ----------
    value : str or None
        Comma-separated toolset names. ``None`` takes the default; an
        empty string enables none, which is legal.

    Returns
    -------
    frozenset of str
        Enabled toolsets.

    Raises
    ------
    RuntimeError
        If a name is not a toolset. A typo silently falling back is how
        a narrowed surface quietly becomes a wider one.
    """
    if value is None:
        return DEFAULT_TOOLSETS
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = [name for name in names if name not in VALID_TOOLSETS]
    if unknown:
        msg = (
            f"LIBTMUX_TOOLSETS names unknown toolsets: {', '.join(unknown)}. "
            f"Valid toolsets: {', '.join(VALID_TOOLSETS)}."
        )
        raise RuntimeError(msg)
    return frozenset(names)


def _resolve_tool_names(value: str | None) -> frozenset[str]:
    """Return a comma-separated tool-name list as a set."""
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _reject_retired_safety_env() -> None:
    """Fail startup when ``LIBTMUX_SAFETY`` is still set.

    The former setting selected an ordered ladder that read as a
    permission system and was not one. Ignoring the variable would
    silently widen a surface an operator believes is narrow.
    """
    if "LIBTMUX_SAFETY" not in os.environ:
        return
    msg = (
        "LIBTMUX_SAFETY has been removed. Tools are grouped into the "
        f"unordered toolsets {', '.join(VALID_TOOLSETS)}; select them with "
        "LIBTMUX_TOOLSETS. The nearest equivalents are "
        "LIBTMUX_TOOLSETS=inspect, LIBTMUX_TOOLSETS=inspect,manage,execute, "
        "and LIBTMUX_TOOLSETS=inspect,manage,execute,teardown."
    )
    raise RuntimeError(msg)


_reject_retired_safety_env()
_toolsets = _resolve_toolsets(os.environ.get("LIBTMUX_TOOLSETS"))
_extra_tools = _resolve_tool_names(os.environ.get("LIBTMUX_TOOLS"))
_excluded_tools = _resolve_tool_names(os.environ.get("LIBTMUX_EXCLUDE_TOOLS"))
_suppress_history = _resolve_suppress_history(
    os.environ.get("LIBTMUX_SUPPRESS_HISTORY")
)
_wait_max_seconds = _resolve_wait_max_seconds(os.environ.get(WAIT_MAX_SECONDS_ENV))

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
    Validates named tool includes and exclusions against the transformed
    catalog, then verifies that a ``tmux`` binary is on ``PATH``. Without
    the binary probe, tools fail at first call with a generic
    ``TmuxCommandNotFound`` deep inside libtmux. Failing at server start
    instead surfaces a clear cold-start error before tool traffic arrives.

    Shutdown
    --------
    Clears the process-wide :data:`_server_cache` so repeated test runs don't
    share stale Server references and HTTP-transport reload cycles start clean.
    Shutdown sends no tmux commands. Buffer tools expose explicit cleanup, and
    a process-wide prefix does not prove which MCP instance owns a buffer.
    """
    registered_tool_names = {
        tool.name for tool in await super(FastMCP, _app).list_tools()
    }
    for variable, names in (
        ("LIBTMUX_TOOLS", _extra_tools),
        ("LIBTMUX_EXCLUDE_TOOLS", _excluded_tools),
    ):
        # FastMCP.list_tools() hides disabled tools. Its provider lookup
        # retains them and applies the same transforms, including optional
        # prompt-as-tool adapters.
        unknown = sorted(names - registered_tool_names)
        if unknown:
            msg = f"{variable} names unknown tools: {', '.join(unknown)}"
            raise RuntimeError(msg)

    if shutil.which("tmux") is None:
        msg = "tmux binary not found on PATH"
        raise RuntimeError(msg)
    try:
        yield
    finally:
        _server_cache.clear()


mcp = FastMCP(
    name="tmux",
    version=__version__,
    instructions=_build_instructions(
        toolsets=_toolsets,
        suppress_history=_suppress_history,
    ),
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
    #      audit + toolset pair: both depend on exception semantics
    #      (audit catches to record outcome=error, and toolset denials
    #      must propagate as exceptions for audit to record them), so
    #      converting the exception to a result any deeper would
    #      silently break both.
    #   4. AuditMiddleware — outside ToolsetMiddleware so refusal
    #      events (which raise ExpectedToolError before call_next inside
    #      Toolset) are still logged with outcome=error. Without this
    #      ordering, denied access attempts would silently bypass the
    #      audit log — a security-observability gap.
    #   5. ToolsetMiddleware — innermost gate (fail-closed). Refusals
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
        ToolsetMiddleware(_toolsets, _extra_tools, _excluded_tools),
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
    # Publish the resolved wait ceiling to the wait tool module. Same
    # shape as the history default above: server owns env resolution,
    # tool modules never import server globals.
    _configure_wait_ceiling(_wait_max_seconds)
    register_resources(mcp)
    register_prompts(mcp)
    _mcp_registered = True


def _enable_allowed_tools() -> None:
    """Apply FastMCP's visibility gate for the enabled toolsets."""
    global _mcp_visibility_configured
    if _mcp_visibility_configured:
        return

    # FastMCP's tag and name visibility is the primary wire filter;
    # ToolsetMiddleware repeats classification as defense in depth for
    # tools that reach dispatch.
    mcp.disable(components={"tool"})
    if _toolsets:
        mcp.enable(tags=set(_toolsets), components={"tool"})
    for name in _extra_tools:
        mcp.enable(components={"tool"}, names={name})
    if _excluded_tools:
        mcp.disable(components={"tool"}, names=set(_excluded_tools))
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
