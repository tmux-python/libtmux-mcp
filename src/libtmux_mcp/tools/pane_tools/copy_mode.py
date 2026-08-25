"""Copy-mode entry / exit tools."""

from __future__ import annotations

import typing as t

from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneInfo,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane


def _run_copy_mode_cmd(pane: Pane, command: str, *, repeat: int | None = None) -> None:
    """Send one ``-X`` copy-mode command, raising if tmux rejected it.

    ``Pane.send_keys(copy_mode_cmd=...)`` discards tmux's result, so
    cancelling a pane that is not in a mode came back as a completed
    operation and the returned ``PaneInfo`` read like confirmation the
    pane had left copy mode. tmux says ``not in a mode`` and exits 1.

    No ``--`` here, unlike the ordinary send path: every *command* is a
    module constant, never caller text.
    """
    args = ["send-keys"]
    if repeat is not None:
        args.extend(("-N", str(repeat)))
    args.extend(("-X", command))
    result = pane.cmd(*args)
    if result.returncode != 0 or result.stderr:
        detail = " ".join(result.stderr).strip() if result.stderr else ""
        msg = f"copy-mode command {command!r} failed: {detail or 'tmux exited 1'}"
        raise ExpectedToolError(msg)


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
    pane.copy_mode()
    if scroll_up is not None and scroll_up > 0:
        _run_copy_mode_cmd(pane, "scroll-up", repeat=scroll_up)
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
    _run_copy_mode_cmd(pane, "cancel")
    pane.refresh()
    return _serialize_pane(pane)
