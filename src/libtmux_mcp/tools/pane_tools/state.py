"""Shared tmux pane state helpers for read and wait tools."""

from __future__ import annotations

import typing as t

from libtmux_mcp._utils import ExpectedToolError

if t.TYPE_CHECKING:
    from libtmux.pane import Pane


class _PaneState(t.NamedTuple):
    """Per-read snapshot of tmux pane grid and lifecycle state.

    Read in one ``display-message`` round-trip so callers avoid
    growing subprocess cost linearly with every required format field.
    ``history_size + cursor_y`` gives the absolute tmux grid row of
    the current cursor.

    Wire format parsed by :func:`_read_pane_state`::

        #{history_size}|#{cursor_y}|#{pane_height}|#{pane_pid}|#{pane_dead}

    Fields are ``|``-separated: the first three are non-negative
    integers, ``pane_pid`` is a decimal PID string, and ``pane_dead``
    is the literal ``"0"`` or ``"1"``.
    """

    history_size: int
    cursor_y: int
    pane_height: int
    pane_pid: str
    pane_dead: bool


def _read_pane_state(pane: Pane) -> _PaneState:
    """Return a :class:`_PaneState` snapshot for ``pane``.

    Combines the tmux state reads needed by wait and incremental
    capture tools into a single ``display-message`` call. ``pane_pid``
    and ``pane_dead`` surface respawn-pane and pane-death events that
    invalidate cursor or baseline anchors.
    """
    stdout = pane.display_message(
        "#{history_size}|#{cursor_y}|#{pane_height}|#{pane_pid}|#{pane_dead}",
        get_text=True,
    )
    raw = stdout[0] if stdout else "0|0|0||0"
    hs, cy, sy, pid, dead = raw.split("|", 4)
    return _PaneState(
        history_size=int(hs),
        cursor_y=int(cy),
        pane_height=int(sy),
        pane_pid=pid,
        pane_dead=dead == "1",
    )


def _raise_if_pane_lifecycle_changed(
    pane: Pane, state: _PaneState, baseline_pid: str
) -> None:
    """Raise ``ExpectedToolError`` when a cursor or wait baseline is invalid."""
    if state.pane_dead:
        msg = f"pane {pane.pane_id} died; cursor/baseline anchor is no longer valid"
        raise ExpectedToolError(msg)
    if state.pane_pid != baseline_pid:
        msg = (
            f"pane {pane.pane_id} was respawned "
            f"(pid {baseline_pid} -> {state.pane_pid}); "
            "cursor/baseline anchor is no longer valid"
        )
        raise ExpectedToolError(msg)


def _read_history_limit(pane: Pane) -> int:
    """Read the pane's ``history-limit`` once.

    Fixed at pane creation — a retroactive ``set-option history-limit``
    only takes effect in tmux 3.7+ (commit ``e7b1575``); older versions
    require a new pane.  Safe to cache for the lifetime of a single
    wait or capture operation.  Kept separate from :func:`_read_pane_state`
    so per-tick reads do not pay for a value that never changes between
    ticks.
    """
    stdout = pane.display_message("#{history_limit}", get_text=True)
    raw = stdout[0] if stdout else "0"
    return int(raw)
