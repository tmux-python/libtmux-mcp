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


def _scrollable_rows(pane: Pane) -> int:
    """Rows a copy-mode cursor can move up before it stops moving.

    History plus the visible screen: above that the cursor is already at
    the top of the grid and every further step is a no-op.
    """
    stdout = pane.display_message("#{history_size} #{pane_height}", get_text=True)
    if not stdout:
        msg = (
            f"pane {pane.pane_id} could not be measured, so a repeat count "
            "cannot be bounded safely"
        )
        raise ExpectedToolError(msg)
    try:
        history, height = (int(part) for part in stdout[0].split())
    except ValueError:
        msg = f"pane {pane.pane_id} reported an unreadable size: {stdout[0]!r}"
        raise ExpectedToolError(msg) from None
    return history + height


def _run_copy_mode_cmd(pane: Pane, command: str, *, repeat: int | None = None) -> None:
    """Send one ``-X`` copy-mode command, raising if tmux rejected it.

    ``Pane.send_keys(copy_mode_cmd=...)`` discards tmux's result, so
    cancelling a pane that is not in a mode came back as a completed
    operation and the returned ``PaneInfo`` read like confirmation the
    pane had left copy mode. tmux says ``not in a mode`` and exits 1.

    ``repeat`` is clamped here rather than by the caller, because tmux's
    ``-N`` reaches an unbounded loop in the single-threaded server:
    ``window_copy_cmd_scroll_up`` runs ``for (; np != 0; np--)`` with no
    reference to how much scrollback exists. Measured at ~30us an
    iteration on a pane with NO history, where every iteration after the
    first is a no-op that still costs full price:

        scroll_up      1,000 -> 0.07s
        scroll_up    100,000 -> 3.0s
        scroll_up 10,000,000 -> still spinning at 30s

    It wedges the whole server rather than the caller. A client-side
    timeout cannot help: three probe servers killed at the CLIENT's 40s
    timeout were still burning CPU when reaped later, at 422s, 289s and
    159s, and ``kill-server`` on the same socket never got through
    either. So the bound has to be applied before dispatch.

    Clamping is not a silent substitution -- the resulting pane state is
    identical, because the discarded iterations could not move anything.
    Measured on a pane with 192 rows of history: ``scroll_up=5`` lands
    at 5, ``50`` at 50, and ``1_000_000_000`` at 192, which is where the
    unclamped call also ended up.

    No ``--`` here, unlike the ordinary send path: every *command* is a
    module constant, never caller text.
    """
    args = ["send-keys"]
    if repeat is not None:
        args.extend(("-N", str(min(repeat, _scrollable_rows(pane)))))
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
    # Validated before entering, not after: a rejected scroll_up used to
    # leave the pane in copy mode anyway, so the error described a call
    # that had already half-happened.
    if scroll_up is not None and scroll_up < 0:
        msg = f"scroll_up must be zero or greater (received {scroll_up})"
        raise ExpectedToolError(msg)
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
