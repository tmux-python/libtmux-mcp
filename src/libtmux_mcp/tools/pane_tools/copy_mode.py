"""Copy-mode entry / exit tools."""

from __future__ import annotations

from libtmux_mcp._utils import (
    _get_server,
    _resolve_pane,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneInfo,
)


@handle_tool_errors
def enter_copy_mode(
    pane_id: str | None = None,
    scroll_up: int | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Enter copy mode in a tmux pane, optionally scrolling up.

    Use to navigate scrollback history. After entering copy mode, use
    snapshot_pane to read the scroll_position and content.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    scroll_up : int, optional
        Number of lines to scroll up immediately after entering copy mode.
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane info.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.cmd("copy-mode")
    if scroll_up is not None and scroll_up > 0:
        pane.cmd(
            "send-keys",
            "-X",
            "-N",
            str(scroll_up),
            "scroll-up",
        )
    pane.refresh()
    return _serialize_pane(pane)


@handle_tool_errors
def exit_copy_mode(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Exit copy mode in a tmux pane.

    Returns the pane to normal mode. Use after scrolling through
    scrollback history.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane info.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.cmd("send-keys", "-X", "cancel")
    pane.refresh()
    return _serialize_pane(pane)
