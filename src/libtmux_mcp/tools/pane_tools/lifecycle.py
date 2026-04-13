"""Pane lifecycle tools: kill, title, info."""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _caller_is_on_server,
    _get_caller_identity,
    _get_server,
    _resolve_pane,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneInfo,
)


@handle_tool_errors
def kill_pane(
    pane_id: str,
    socket_name: str | None = None,
) -> str:
    """Kill (close) a tmux pane. Requires exact pane_id (e.g. '%5').

    Use to clean up panes no longer needed. To remove an entire window
    and all its panes, use kill_window instead.

    Parameters
    ----------
    pane_id : str
        Pane ID (e.g. '%1'). Required — no fallback resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    server = _get_server(socket_name=socket_name)
    caller = _get_caller_identity()
    if (
        caller is not None
        and caller.pane_id == pane_id
        and _caller_is_on_server(server, caller)
    ):
        msg = (
            "Refusing to kill the pane running this MCP server. "
            "Use a manual tmux command if intended."
        )
        raise ToolError(msg)

    pane = _resolve_pane(server, pane_id=pane_id)
    pid = pane.pane_id
    pane.kill()
    return f"Pane killed: {pid}"


@handle_tool_errors
def set_pane_title(
    title: str,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Set the title of a tmux pane.

    Use titles to label panes for later identification via list_panes or get_pane_info.

    Parameters
    ----------
    title : str
        The new pane title.
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane object.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.set_title(title)
    return _serialize_pane(pane)


@handle_tool_errors
def get_pane_info(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Get detailed information about a tmux pane.

    Use this for metadata (PID, path, dimensions) without reading terminal content.
    To read what is displayed in the pane, use capture_pane instead.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane details.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    return _serialize_pane(pane)
