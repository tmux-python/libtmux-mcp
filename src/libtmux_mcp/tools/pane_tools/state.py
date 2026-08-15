"""Shared tmux pane state helpers for read and wait tools."""

from __future__ import annotations

import typing as t

from libtmux_mcp._utils import ExpectedToolError


class _PaneState(t.NamedTuple):
    """Per-read snapshot of tmux pane grid and lifecycle state.

    Read in one ``display-message`` round-trip so callers avoid
    growing subprocess cost linearly with every required format field.
    ``history_size + cursor_y`` gives the absolute tmux grid row of
    the current cursor.

    Wire format parsed by :func:`_parse_pane_state`::

        #{history_size}|#{cursor_y}|#{pane_height}|#{pane_pid}|#{pane_dead}
        |#{alternate_on}

    Fields are ``|``-separated: the first three are non-negative
    integers, ``pane_pid`` is a decimal PID string, and ``pane_dead``
    and ``alternate_on`` are the literal ``"0"`` or ``"1"``.

    ``alternate_on`` rides along free in the same round-trip. It is
    reported, never acted on: a pane on the alternate screen has been
    handed to a full-screen program that owns and repaints the whole
    grid, so "text appeared below the cursor" stops meaning anything
    there. Measured on tmux 3.7b, ``capture-pane -S`` still returns
    real main-screen scrollback while ``alternate_on=1`` — the grid
    is shared, so the anchor stays arithmetically valid and only the
    rows beneath it have been overwritten by paint.
    """

    history_size: int
    cursor_y: int
    pane_height: int
    pane_pid: str
    pane_dead: bool
    alternate_on: bool = False


#: tmux format string whose single output line :func:`_parse_pane_state`
#: decodes. Exposed as a constant because the wait tools issue this read
#: through a timeout-bounded ``subprocess.run`` rather than libtmux (whose
#: ``Popen.communicate()`` has no timeout and can wedge a worker
#: thread). It is a fixed literal — no caller-supplied text is ever
#: interpolated into a tmux format string, because tmux's format
#: parser treats ``#`` and ``}`` structurally and a pattern containing
#: either silently corrupts the surrounding fields.
PANE_STATE_FORMAT = (
    "#{history_size}|#{cursor_y}|#{pane_height}|#{pane_pid}|#{pane_dead}"
    "|#{alternate_on}"
)

#: ``history-limit`` read, split out for the same reason.
HISTORY_LIMIT_FORMAT = "#{history_limit}"


def _parse_pane_state(raw: str) -> _PaneState:
    """Parse one :data:`PANE_STATE_FORMAT` line into a :class:`_PaneState`."""
    # ``maxsplit`` is one below the field count so a pane_pid or a
    # future field containing ``|`` cannot shift the parse. Older tmux
    # builds that do not know ``alternate_on`` emit the literal format
    # text rather than a value, so treat anything but ``"1"`` as off
    # instead of raising — this read is on the hot poll path and must
    # degrade, not fail, across the CI tmux version matrix.
    parts = raw.split("|", 5)
    hs, cy, sy, pid, dead = parts[:5]
    alternate = parts[5] if len(parts) > 5 else "0"
    return _PaneState(
        history_size=int(hs),
        cursor_y=int(cy),
        pane_height=int(sy),
        pane_pid=pid,
        pane_dead=dead == "1",
        alternate_on=alternate == "1",
    )


def _raise_if_pane_lifecycle_changed(
    pane_id: str | None, state: _PaneState, baseline_pid: str
) -> None:
    """Raise ``ExpectedToolError`` when a cursor or wait baseline is invalid.

    Takes the pane *id* rather than a :class:`~libtmux.pane.Pane` so the
    wait tools can call it after they stop holding a live libtmux
    object — they resolve the target once, off the event loop, then
    work from the id string alone.
    """
    if state.pane_dead:
        msg = f"pane {pane_id} died; cursor/baseline anchor is no longer valid"
        raise ExpectedToolError(msg)
    if state.pane_pid != baseline_pid:
        msg = (
            f"pane {pane_id} was respawned "
            f"(pid {baseline_pid} -> {state.pane_pid}); "
            "cursor/baseline anchor is no longer valid"
        )
        raise ExpectedToolError(msg)
