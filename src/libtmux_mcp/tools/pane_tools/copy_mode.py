"""Copy-mode entry / exit tools."""

from __future__ import annotations

import typing as t

from libtmux.common import get_version_str, has_gte_version

from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    BufferContent,
    PaneInfo,
)
from libtmux_mcp.tools.buffer_tools import (
    SHOW_BUFFER_DEFAULT_MAX_LINES,
    _allocate_buffer_name,
    _read_buffer,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane


#: Floor for :func:`copy_selection`. Below this, ``copy-selection`` kills
#: the tmux SERVER -- not the pane, not the command -- taking every
#: session on it. All four conditions hold in a default install:
#:
#:     tmux 3.2a or 3.3a       3.4+ is unaffected
#:     a client attached       detached survives
#:     set-clipboard on        the default is ``external`` on 3.2a..3.7c
#:     clipboard-capable TERM  xterm-256color dies, screen-256color does not
#:
#: Documenting it is not enough: this tool reads a human's selection, so
#: an attached client at default settings IS its use case. Refused rather
#: than worked around -- ``set-clipboard off`` for the duration prevents
#: the crash but mutates a server-global option, and two concurrent calls
#: would race to restore it.
_COPY_SELECTION_MIN_VERSION = "3.4"

#: From this version ``copy-selection`` takes ``-C``, suppressing the OSC
#: 52 write so an agent reading a person's selection does not overwrite
#: their system clipboard. On 3.4 and 3.5 the flag does not exist --
#: those copy-mode commands parse by minargs/maxargs -- and ``-C`` would
#: be taken as the buffer-name prefix.
_COPY_SELECTION_NO_CLIPBOARD_VERSION = "3.6"


def _copy_selection_flags(tmux_bin: str | None) -> tuple[str, ...]:
    """Return the ``copy-selection`` flags the driven tmux supports."""
    if has_gte_version(_COPY_SELECTION_NO_CLIPBOARD_VERSION, tmux_bin=tmux_bin):
        return ("-C",)
    return ()


def _raise_if_copy_selection_unsupported(tmux_bin: str | None) -> None:
    """Refuse on a tmux where copying a selection kills the server."""
    if has_gte_version(_COPY_SELECTION_MIN_VERSION, tmux_bin=tmux_bin):
        return
    msg = (
        f"copy_selection requires tmux {_COPY_SELECTION_MIN_VERSION} or newer "
        f"(this server runs {get_version_str(tmux_bin=tmux_bin)})"
    )
    raise ExpectedToolError(
        msg,
        suggestion=(
            "On tmux 3.2a and 3.3a, copy-selection kills the tmux server "
            "-- and every session on it -- when a client is attached with "
            "a clipboard-capable terminal, which is the default. Use "
            "capture_pane to read the pane's text instead."
        ),
    )


#: ``pane_mode`` for copy mode. Distinct from ``pane_in_mode``, which
#: is also 1 for tree-mode, client-mode and the rest.
_COPY_MODE = "copy-mode"

#: One less than the 64 an ordinary logical buffer label may use: tmux
#: appends an index to the prefix it is handed, and the resulting name
#: still has to satisfy the MCP buffer-name shape.
_COPY_LABEL_MAX = 63


def _validate_copy_label(logical_name: str | None) -> str | None:
    """Reject a label that tmux's appended index would push over length."""
    if logical_name is not None and len(logical_name) > _COPY_LABEL_MAX:
        msg = (
            f"logical_name is limited to {_COPY_LABEL_MAX} characters here "
            f"(received {len(logical_name)}), because tmux appends an index "
            "to the buffer name it is given."
        )
        raise ExpectedToolError(msg)
    return logical_name


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


def _run_copy_mode_cmd(
    pane: Pane,
    command: str,
    *,
    repeat: int | None = None,
    flags: tuple[str, ...] = (),
    argument: str | None = None,
) -> None:
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
    module constant, never caller text. ``argument`` is the one value
    that varies, and it is a server-allocated buffer name -- prefix plus
    hex plus a restricted label -- so it cannot open with ``-``. It goes
    in its own argv element because tmux parses the copy-mode command
    and its argument separately.
    """
    args = ["send-keys"]
    if repeat is not None:
        args.extend(("-N", str(min(repeat, _scrollable_rows(pane)))))
    args.extend(("-X", command))
    args.extend(flags)
    if argument is not None:
        args.append(argument)
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


def _copy_mode_state(pane: Pane) -> tuple[str, str]:
    """Return ``(pane_mode, selection_present)`` in one round trip."""
    stdout = pane.display_message("#{pane_mode}\t#{selection_present}", get_text=True)
    if not stdout:
        msg = f"pane {pane.pane_id} did not report its mode"
        raise ExpectedToolError(msg)
    mode, _, selection = stdout[0].partition("\t")
    return mode, selection


@handle_tool_errors
def copy_selection(
    pane_id: str | None = None,
    logical_name: str | None = None,
    max_lines: int | None = SHOW_BUFFER_DEFAULT_MAX_LINES,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> BufferContent:
    """Read the text currently SELECTED in a pane's copy mode.

    Reads what is selected **right now**, which is the case
    ``capture_pane`` cannot reach: a person attached to the session has
    highlighted something, and the agent is being asked about that
    highlight rather than about the pane. The selection survives idle
    time, cursor movement and further process output -- only leaving
    copy mode clears it -- so there is nothing to race.

    Not "read what they just copied". A human who pressed Enter (or
    ``y``) has already left copy mode, and that text is in tmux's own
    ``buffer0``, which this server refuses to read for the reason given
    in :func:`~libtmux_mcp.tools.buffer_tools.delete_buffer` -- tmux
    buffers may hold clipboard history. Most key bindings copy AND
    cancel, so this is the common human ending; the answer for it is to
    ask the person to select again, not to widen that boundary.

    The copy lands in a fresh MCP-namespaced buffer, so the returned
    ``buffer_name`` works with ``paste_buffer`` (to move a selection
    into another pane), ``show_buffer`` and ``delete_buffer``. The
    selection itself is left intact.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    logical_name : str, optional
        Short label for the buffer this allocates, as in
        ``load_buffer``. Limited to 63 characters here, one less than
        elsewhere, because tmux appends an index to the name it is given.
    max_lines : int or None
        Maximum number of lines to return, tail-preserved. Pass ``None``
        for no truncation. The buffer always holds the full selection
        whatever this is set to.
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
    BufferContent
        ``buffer_name``, the selected ``content``, and the truncation
        fields -- the same shape ``show_buffer`` returns.
    """
    server = _get_server(socket_name=socket_name)
    # Checked against the binary THIS server drives, not the system one:
    # LIBTMUX_TMUX_BIN can point the whole stack at another build, and a
    # guard that read a different tmux than it protects would be the
    # exact failure it exists to prevent.
    tmux_bin: str | None = getattr(server, "tmux_bin", None)
    _raise_if_copy_selection_unsupported(tmux_bin)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    mode, selection = _copy_mode_state(pane)
    # Keyed on pane_mode, not pane_in_mode: choose-tree and the other
    # modes also set the flag, and `-X copy-selection` in tree-mode
    # would be sent to a mode that has no selection to give.
    if mode != _COPY_MODE:
        msg = f"pane {pane.pane_id} is not in copy mode" + (
            f" (it is in {mode})" if mode else ""
        )
        raise ExpectedToolError(
            msg,
            suggestion=(
                "Only a live selection is readable. If a person just "
                "copied with Enter or y, they have already left copy "
                "mode and the text is in tmux's own buffer, which this "
                "server does not read. Use capture_pane for pane text, "
                "or enter_copy_mode to make a selection yourself."
            ),
        )
    # tmux exits 0 for copy-selection with nothing selected and creates no
    # buffer, so this is what keeps the tool from handing back a name that
    # does not exist. Compared against "1": on the floor an absent
    # selection reads as the empty string, not "0".
    if selection != "1":
        msg = f"pane {pane.pane_id} is in copy mode but nothing is selected"
        raise ExpectedToolError(
            msg,
            suggestion=(
                "A selection that has not moved off its start counts as "
                "absent. Begin one with send_keys(keys='-X begin-selection') "
                "and move the cursor before copying."
            ),
        )

    prefix = _allocate_buffer_name(_validate_copy_label(logical_name))
    _run_copy_mode_cmd(
        pane,
        "copy-selection-no-clear",
        flags=_copy_selection_flags(tmux_bin),
        argument=prefix,
    )
    # tmux appends an index to the prefix. The name is not assumed:
    # reading it back is also what proves the prefix argument was
    # honoured, rather than the copy landing in an unnamed buffer
    # outside the MCP namespace.
    try:
        return _read_buffer(server, f"{prefix}0", max_lines)
    except ExpectedToolError as err:
        msg = f"the selection in pane {pane.pane_id} was not captured to {prefix}0"
        raise ExpectedToolError(msg) from err
