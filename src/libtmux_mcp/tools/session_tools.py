"""MCP tools for tmux session operations."""

from __future__ import annotations

import typing as t

from libtmux.constants import WindowDirection

from libtmux_mcp._history import _prepare_spawn_environment
from libtmux_mcp._utils import (
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    DISCOVERY_META,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    ExpectedToolError,
    _apply_filters,
    _caller_is_on_server,
    _get_caller_identity,
    _get_server,
    _resolve_session,
    _serialize_session,
    _serialize_window,
    handle_tool_errors,
)
from libtmux_mcp.models import SessionInfo, WindowInfo

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


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
    suppress_history: bool = False,
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
        Working directory for the new window.
    attach : bool, optional
        Whether to make the new window active.
    direction : str, optional
        Window placement direction.
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.
    environment : dict or str, optional
        Per-process environment as a mapping or JSON object string. Values do
        not modify the tmux session environment.
    suppress_history : bool
        For MCP calls, omission uses the server's LIBTMUX_SUPPRESS_HISTORY
        default; an explicit value overrides it. Direct Python calls default
        to False. Startup files may override these controls.

    Returns
    -------
    WindowInfo
        Serialized window object.
    """
    spawn_environment = _prepare_spawn_environment(
        environment,
        suppress_history=suppress_history,
    )
    server = _get_server(socket_name=socket_name)
    session = _resolve_session(server, session_name=session_name, session_id=session_id)
    kwargs: dict[str, t.Any] = {}
    if window_name is not None:
        kwargs["window_name"] = window_name
    if start_directory is not None:
        kwargs["start_directory"] = start_directory
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
    session = session.rename_session(new_name)
    return _serialize_session(session)


@handle_tool_errors
def kill_session(
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> str:
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
        annotations=ANNOTATIONS_RO,
        tags={TAG_READONLY},
        meta=DISCOVERY_META,
    )(list_windows)
    mcp.tool(
        title="Get tmux Session Info", annotations=ANNOTATIONS_RO, tags={TAG_READONLY}
    )(get_session_info)
    mcp.tool(
        title="Create tmux Window", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING}
    )(create_window)
    mcp.tool(
        title="Rename tmux Session",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(rename_session)
    mcp.tool(
        title="Kill tmux Session",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(kill_session)
    mcp.tool(
        title="Select tmux Window",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(select_window)
