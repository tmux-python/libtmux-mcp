"""MCP tools for tmux session operations."""

from __future__ import annotations

import typing as t

from fastmcp import Context
from libtmux.constants import WindowDirection
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from libtmux_mcp._history import _prepare_spawn_environment
from libtmux_mcp._utils import (
    ANNOTATIONS_AMBIENT_UNKNOWN,
    DISCOVERY_META,
    TOOLSET_EXECUTE,
    TOOLSET_INSPECT,
    TOOLSET_MANAGE,
    TOOLSET_TEARDOWN,
    ExpectedToolError,
    _apply_filters,
    _caller_is_on_server,
    _escape_tmux_format,
    _get_caller_identity,
    _get_server,
    _prepare_start_directory,
    _resolve_session,
    _serialize_session,
    _serialize_window,
    handle_tool_errors,
    handle_tool_errors_async,
)
from libtmux_mcp.models import SessionInfo, WindowInfo

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from libtmux.session import Session


@handle_tool_errors
def list_windows(
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
    filters: dict[str, str] | str | None = None,
) -> list[WindowInfo]:
    """List tmux windows (terminal tabs) in a session, or across the server.

    Use for tmux windows — 'current window', 'this tab' (when terminal-
    contextual) — not browser tabs or desktop windows. Only searches
    window metadata (name, index, layout); to search the actual visible
    terminal text, use search_panes.

    Parameters
    ----------
    session_name : str, optional
        Session name to look up. If omitted along with session_id,
        returns windows from all sessions.
    session_id : str, optional
        Session ID (e.g. '$1') to look up.
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.
    filters : dict or str, optional
        Django-style filters as a dict (e.g. ``{"window_name__contains": "dev"}``)
        or as a JSON string. Some MCP clients require the string form.

    Returns
    -------
    list[WindowInfo]
        List of serialized window objects.
    """
    server = _get_server(socket_name=socket_name)
    if session_name is not None or session_id is not None:
        session = _resolve_session(
            server, session_name=session_name, session_id=session_id
        )
        windows = session.windows
    else:
        windows = server.windows
    return _apply_filters(windows, filters, _serialize_window)


# get_session_info completes the core-tmux-hierarchy symmetry alongside
# get_window_info / get_pane_info / get_server_info. Bounded to the four
# hierarchy levels — see the same note in window_tools.get_window_info.
@handle_tool_errors
def get_session_info(
    session_id: str | None = None,
    session_name: str | None = None,
    socket_name: str | None = None,
) -> SessionInfo:
    """Return metadata for a single tmux session (ID, name, window count, activity).

    Use this instead of list_sessions + filter when you only need one
    session's info. Resolves by session_id first; falls back to
    session_name.

    Parameters
    ----------
    session_id : str, optional
        Session ID (e.g. '$0').
    session_name : str, optional
        Session name.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    SessionInfo
        Serialized session metadata.
    """
    server = _get_server(socket_name=socket_name)
    session = _resolve_session(server, session_name=session_name, session_id=session_id)
    return _serialize_session(session)


@handle_tool_errors
def create_window(
    session_name: str | None = None,
    session_id: str | None = None,
    window_name: str | None = None,
    start_directory: str | None = None,
    attach: bool = False,
    direction: t.Literal["before", "after"] | None = None,
    socket_name: str | None = None,
    *,
    environment: dict[str, str] | str | None = None,
    suppress_persistent_history: bool = False,
) -> WindowInfo:
    """Create a new window in a tmux session.

    Creates a window with one pane. Use split_window to add more panes afterward.

    Parameters
    ----------
    session_name : str, optional
        Session name to look up.
    session_id : str, optional
        Session ID (e.g. '$1') to look up.
    window_name : str, optional
        Name for the new window.
    start_directory : str, optional
        Existing directory to start in. ``~`` expands; a relative path
        resolves against the MCP server process's directory.
    attach : bool, optional
        Whether to make the new window active.
    direction : str, optional
        Window placement direction.
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.
    environment : dict or str, optional
        Per-process environment as a mapping or JSON object string. Values do
        not modify the tmux session environment. Each item becomes a tmux
        ``-e`` launch option. Values may be visible to host process inspection
        in the tmux client argv during launch and in the child environment
        afterward; MCP audit redaction does not hide either surface. Pass
        credential references, not literal credentials.
    suppress_persistent_history : bool
        Whether to suppress persistent history for the spawned shell. Defaults
        to False for MCP and direct Python calls. This per-call option does not
        inherit LIBTMUX_SUPPRESS_HISTORY. Startup files may override these
        controls.

    Returns
    -------
    WindowInfo
        Serialized window object.
    """
    spawn_environment = _prepare_spawn_environment(
        environment,
        suppress_persistent_history=suppress_persistent_history,
    )
    server = _get_server(socket_name=socket_name)
    session = _resolve_session(server, session_name=session_name, session_id=session_id)
    kwargs: dict[str, t.Any] = {}
    if window_name is not None:
        kwargs["window_name"] = _escape_tmux_format(window_name)
    prepared_start_directory = _prepare_start_directory(start_directory)
    if prepared_start_directory is not None:
        kwargs["start_directory"] = prepared_start_directory
    kwargs["attach"] = attach
    if direction is not None:
        direction_map: dict[str, WindowDirection] = {
            "before": WindowDirection.Before,
            "after": WindowDirection.After,
        }
        resolved = direction_map.get(direction)
        if resolved is None:
            valid = ", ".join(sorted(direction_map))
            msg = f"Invalid direction: {direction!r}. Valid: {valid}"
            raise ExpectedToolError(msg)
        kwargs["direction"] = resolved
    if spawn_environment is not None:
        kwargs["environment"] = spawn_environment
    window = session.new_window(**kwargs)
    return _serialize_window(window)


@handle_tool_errors
def rename_session(
    new_name: str,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> SessionInfo:
    """Rename a tmux session.

    Use when a session's purpose has changed. Existing pane_id references
    remain valid after renaming.

    Parameters
    ----------
    new_name : str
        New name for the session.
    session_name : str, optional
        Current session name to look up.
    session_id : str, optional
        Session ID (e.g. '$1') to look up.
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.

    Returns
    -------
    SessionInfo
        Serialized session object.
    """
    server = _get_server(socket_name=socket_name)
    session = _resolve_session(server, session_name=session_name, session_id=session_id)
    session = session.rename_session(_escape_tmux_format(new_name))
    return _serialize_session(session)


#: Key of the confirmation request in a 2026-07-28 ``InputRequiredResult``.
_CONFIRM_KEY = "confirm"


def _session_identity(session: Session) -> str:
    """Return an identity that survives a tmux server restart.

    ``session_id`` alone does not: tmux numbers sessions from ``$0`` on
    every server start, so killing the last session and creating one
    reuses the id. ``start_time`` has one-second resolution; ``pid``
    tells two servers started in the same second apart.
    """
    proc = session.cmd("display-message", "-p", "#{pid}:#{start_time}:#{session_id}")
    return proc.stdout[0] if proc.stdout else ""


async def _confirm_teardown(
    ctx: Context | None,
    message: str,
    target_id: str,
) -> bool | InputRequiredResult | None:
    """Ask the user to confirm a teardown, on either protocol era.

    Returns the answer, an ``InputRequiredResult`` to return on a
    2026-07-28 first round, or ``None`` when the client cannot ask.
    ``target_id`` rides ``request_state`` so the second round refuses a
    name that now resolves to a different object.
    """
    if ctx is None:
        return None
    capabilities = ctx.session.client_capabilities
    if capabilities is None or capabilities.elicitation is None:
        return None
    request = ctx.request_context
    if request is None or request.protocol_version not in MODERN_PROTOCOL_VERSIONS:
        result = await ctx.elicit(message, response_type=bool)
        return result.action == "accept" and bool(result.data)

    responses = ctx.input_responses
    if responses is None:
        params = ElicitRequestFormParams(
            message=message,
            requested_schema={
                "type": "object",
                "properties": {"value": {"type": "boolean", "title": "Confirm"}},
                "required": ["value"],
            },
        )
        return InputRequiredResult(
            result_type="input_required",
            input_requests={
                _CONFIRM_KEY: ElicitRequest(method="elicitation/create", params=params)
            },
            request_state=target_id,
        )
    if ctx.request_state != target_id:
        msg = "Target changed between confirmation rounds; nothing was killed."
        raise ExpectedToolError(msg)
    answer = responses.get(_CONFIRM_KEY)
    if not isinstance(answer, ElicitResult) or answer.action != "accept":
        return False
    content = answer.content if isinstance(answer.content, dict) else {}
    return bool(content.get("value"))


@handle_tool_errors_async
async def kill_session(
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
    ctx: Context | None = None,
) -> str | InputRequiredResult:
    """Kill a tmux session.

    Destroys the session and all its windows and panes. Use kill_window
    to remove a single window instead. Self-kill protection prevents
    killing the session containing this MCP process.

    Parameters
    ----------
    session_name : str, optional
        Session name to look up.
    session_id : str, optional
        Session ID (e.g. '$1') to look up.
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.

    Returns
    -------
    str
        Confirmation message.
    """
    if session_name is None and session_id is None:
        msg = (
            "Refusing to kill without an explicit target. "
            "Provide session_name or session_id."
        )
        raise ExpectedToolError(msg)

    server = _get_server(socket_name=socket_name)
    session = _resolve_session(server, session_name=session_name, session_id=session_id)

    caller = _get_caller_identity()
    if _caller_is_on_server(server, caller) and caller is not None and caller.pane_id:
        caller_pane = server.panes.get(pane_id=caller.pane_id, default=None)
        if caller_pane is not None and caller_pane.session_id == session.session_id:
            msg = (
                "Refusing to kill the session containing this MCP server's pane. "
                "Use a manual tmux command if intended."
            )
            raise ExpectedToolError(msg)

    name = session.session_name or session.session_id
    identity = _session_identity(session)
    confirmed = await _confirm_teardown(
        ctx,
        f"Kill tmux session {name!r} and every process in its panes?",
        target_id=identity,
    )
    if isinstance(confirmed, InputRequiredResult):
        return confirmed
    if confirmed is False:
        msg = f"Session not killed: {name} (the user declined)."
        raise ExpectedToolError(msg)
    # The handshake path held ``session`` across the await; recheck it.
    if confirmed and _session_identity(session) != identity:
        msg = "Target changed between confirmation rounds; nothing was killed."
        raise ExpectedToolError(msg)
    # A client that cannot ask (None) gets no confirmation; the kill proceeds.
    session.kill()
    return f"Session killed: {name}"


@handle_tool_errors
def select_window(
    window_id: str | None = None,
    window_index: str | None = None,
    direction: t.Literal["next", "previous", "last"] | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> WindowInfo:
    """Select (focus) a tmux window by ID, index, or direction.

    Use to navigate between windows. Provide window_id or window_index
    for direct selection, or direction for relative navigation.

    Parameters
    ----------
    window_id : str, optional
        Window ID (e.g. '@1') for direct selection.
    window_index : str, optional
        Window index for direct selection.
    direction : str, optional
        Relative direction: 'next', 'previous', or 'last'.
    session_name : str, optional
        Session name for resolution.
    session_id : str, optional
        Session ID for resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WindowInfo
        The now-active window.
    """
    if window_id is None and window_index is None and direction is None:
        msg = "Provide window_id, window_index, or direction."
        raise ExpectedToolError(msg)

    server = _get_server(socket_name=socket_name)

    if window_id is not None or window_index is not None:
        from libtmux_mcp._utils import _resolve_window

        window = _resolve_window(
            server,
            window_id=window_id,
            window_index=window_index,
            session_name=session_name,
            session_id=session_id,
        )
        window.select()
        return _serialize_window(window)

    # Directional navigation. Each Session method injects `-t
    # $session_id`, returns the new active Window, and raises
    # LibTmuxException on stderr — so the dispatch reduces to a
    # straight lookup with no manual stderr handling.
    session = _resolve_session(server, session_name=session_name, session_id=session_id)
    _NAV = {
        "next": session.next_window,
        "previous": session.previous_window,
        "last": session.last_window,
    }
    assert direction is not None
    fn = _NAV.get(direction)
    if fn is None:
        msg = f"Invalid direction: {direction!r}. Valid: next, previous, last"
        raise ExpectedToolError(msg)
    active_window = fn()
    return _serialize_window(active_window)


def register(mcp: FastMCP) -> None:
    """Register session-level tools with the MCP instance."""
    mcp.tool(
        title="List tmux Windows",
        annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
        tags={TOOLSET_INSPECT},
        meta=DISCOVERY_META,
    )(list_windows)
    mcp.tool(
        title="Get tmux Session Info",
        annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
        tags={TOOLSET_INSPECT},
    )(get_session_info)
    mcp.tool(
        title="Create tmux Window",
        annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
        tags={TOOLSET_EXECUTE},
    )(create_window)
    mcp.tool(
        title="Rename tmux Session",
        annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
        tags={TOOLSET_MANAGE},
    )(rename_session)
    mcp.tool(
        title="Kill tmux Session",
        annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
        tags={TOOLSET_TEARDOWN},
    )(kill_session)
    mcp.tool(
        title="Select tmux Window",
        annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
        tags={TOOLSET_MANAGE},
    )(select_window)
