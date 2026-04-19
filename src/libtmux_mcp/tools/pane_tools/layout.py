"""Pane sizing, selection, and swap tools."""

from __future__ import annotations

import typing as t

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _get_server,
    _resolve_pane,
    _resolve_window,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneInfo,
)


@handle_tool_errors
def resize_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    height: int | None = None,
    width: int | None = None,
    zoom: bool | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Resize a tmux pane.

    Use when adjusting layout for better readability or to fit content.

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
    height : int, optional
        New height in lines.
    width : int, optional
        New width in columns.
    zoom : bool, optional
        Toggle pane zoom. If True, zoom the pane. If False, unzoom.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane object.
    """
    if zoom is not None and (height is not None or width is not None):
        msg = "Cannot combine zoom with height/width"
        raise ToolError(msg)

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    if zoom is not None:
        window = pane.window
        result = window.cmd("display-message", "-p", "#{window_zoomed_flag}")
        is_zoomed = bool(result.stdout) and result.stdout[0] == "1"
        if zoom and not is_zoomed:
            pane.resize(zoom=True)
        elif not zoom and is_zoomed:
            pane.resize(zoom=True)  # toggle off
    else:
        pane.resize(height=height, width=width)
    return _serialize_pane(pane)


@handle_tool_errors
def select_pane(
    pane_id: str | None = None,
    direction: t.Literal["up", "down", "left", "right", "last", "next", "previous"]
    | None = None,
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Select (focus) a tmux pane by ID or direction.

    Use this to navigate between panes. Provide either pane_id for direct
    selection, or direction for relative navigation within a window.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1') for direct selection.
    direction : str, optional
        Relative direction: 'up', 'down', 'left', 'right', 'last'
        (previously active), 'next', or 'previous'.
    window_id : str, optional
        Window ID for directional navigation scope.
    window_index : str, optional
        Window index for directional navigation scope.
    session_name : str, optional
        Session name for resolution.
    session_id : str, optional
        Session ID for resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        The now-active pane.
    """
    if pane_id is None and direction is None:
        msg = "Provide either pane_id or direction."
        raise ToolError(msg)

    server = _get_server(socket_name=socket_name)

    if pane_id is not None:
        pane = _resolve_pane(server, pane_id=pane_id)
        pane.select()
        return _serialize_pane(pane)

    # Directional navigation
    _DIRECTION_FLAGS: dict[str, str] = {
        "up": "-U",
        "down": "-D",
        "left": "-L",
        "right": "-R",
        "last": "-l",
    }

    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )

    assert direction is not None
    if direction in _DIRECTION_FLAGS:
        window.select_pane(_DIRECTION_FLAGS[direction])
    elif direction in ("next", "previous"):
        # Compute the target pane by absolute pane_id rather than using
        # tmux's relative pane-target syntax. Two portability issues
        # motivate this approach:
        # 1. A bare `-t +` / `-t -1` resolves against the attached
        #    client's current window (tmux cmd-find.c), not the window
        #    we're targeting.
        # 2. The scoped form `@window_id.+` / `.-` works on tmux 3.6+
        #    but the relative-offset parser's behavior for prefixed
        #    window targets varies on older releases (tmux 3.2a still
        #    falls back to client curw for `@id.+`). Enumerating
        #    panes and selecting by absolute pane_id sidesteps
        #    tmux-version variation entirely.
        window.refresh()
        panes = list(window.panes)
        active = next((p for p in panes if p.pane_active == "1"), panes[0])
        idx = panes.index(active)
        step = 1 if direction == "next" else -1
        target_pane = panes[(idx + step) % len(panes)]
        server.cmd("select-pane", target=target_pane.pane_id)

    # Query the active pane ID directly from tmux to avoid stale cache
    target = window.window_id or ""
    result = window.cmd("display-message", "-p", "-t", target, "#{pane_id}")
    active_pane_id = result.stdout[0] if result.stdout else None
    if active_pane_id:
        active_pane = server.panes.get(pane_id=active_pane_id, default=None)
        if active_pane is not None:
            return _serialize_pane(active_pane)

    # Fallback
    active_pane = window.active_pane
    assert active_pane is not None
    return _serialize_pane(active_pane)


@handle_tool_errors
def swap_pane(
    source_pane_id: str,
    target_pane_id: str,
    socket_name: str | None = None,
) -> PaneInfo:
    """Swap the positions of two panes.

    Exchanges the visual positions of two panes. Both panes must exist.
    Use this to rearrange pane layout without changing content.

    Parameters
    ----------
    source_pane_id : str
        Pane ID of the first pane (e.g. '%1').
    target_pane_id : str
        Pane ID of the second pane (e.g. '%2').
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        The source pane after swap (now in target's position).
    """
    server = _get_server(socket_name=socket_name)
    # Validate both panes exist
    source = _resolve_pane(server, pane_id=source_pane_id)
    _resolve_pane(server, pane_id=target_pane_id)

    server.cmd("swap-pane", "-s", source_pane_id, "-t", target_pane_id)
    source.refresh()
    return _serialize_pane(source)
