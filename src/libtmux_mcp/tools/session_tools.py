"""MCP tools for tmux session operations."""

from __future__ import annotations

import typing as t

from libtmux.constants import WindowDirection

from libtmux_mcp._utils import (
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
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
    """List windows in a tmux session, or all windows across sessions.

    Only searches window metadata (name, index, layout). To search
    the actual text visible in terminal panes, use search_panes instead.

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


@handle_tool_errors
def create_window(
    session_name: str | None = None,
    session_id: str | None = None,
    window_name: str | None = None,
    start_directory: str | None = None,
    attach: bool = False,
    direction: t.Literal["before", "after"] | None = None,
    socket_name: str | None = None,
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

    Returns
    -------
    WindowInfo
        Serialized window object.
    """
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
            from fastmcp.exceptions import ToolError

            valid = ", ".join(sorted(direction_map))
            msg = f"Invalid direction: {direction!r}. Valid: {valid}"
            raise ToolError(msg)
        kwargs["direction"] = resolved
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
    from fastmcp.exceptions import ToolError

    if session_name is None and session_id is None:
        msg = (
            "Refusing to kill without an explicit target. "
            "Provide session_name or session_id."
        )
        raise ToolError(msg)

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
            raise ToolError(msg)

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
    from fastmcp.exceptions import ToolError

    if window_id is None and window_index is None and direction is None:
        msg = "Provide window_id, window_index, or direction."
        raise ToolError(msg)

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

    # Directional navigation: use the dedicated tmux subcommands so that
    # libtmux's Session.cmd injects `-t $session_id` and the navigation
    # stays scoped to this session (a bare `-t +` resolves against the
    # attached client, not the target session).
    session = _resolve_session(server, session_name=session_name, session_id=session_id)
    _CMD_MAP = {
        "next": "next-window",
        "previous": "previous-window",
        "last": "last-window",
    }
    assert direction is not None
    subcommand = _CMD_MAP.get(direction)
    if subcommand is None:
        msg = f"Invalid direction: {direction!r}. Valid: next, previous, last"
        raise ToolError(msg)
    proc = session.cmd(subcommand)
    if proc.stderr:
        stderr = " ".join(proc.stderr).strip()
        msg = f"tmux {subcommand} failed: {stderr}"
        raise ToolError(msg)

    active_window = session.active_window
    return _serialize_window(active_window)


def register(mcp: FastMCP) -> None:
    """Register session-level tools with the MCP instance."""
    mcp.tool(title="List Windows", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        list_windows
    )
    mcp.tool(
        title="Create Window", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING}
    )(create_window)
    mcp.tool(
        title="Rename Session", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(rename_session)
    mcp.tool(
        title="Kill Session",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(kill_session)
    mcp.tool(
        title="Select Window", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(select_window)
