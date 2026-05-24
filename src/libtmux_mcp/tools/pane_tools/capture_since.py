"""Incremental capture tool for tmux pane observation."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import time
import typing as t
from dataclasses import dataclass

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _get_server,
    _resolve_pane,
    handle_tool_errors_async,
)
from libtmux_mcp.models import CaptureSinceResult
from libtmux_mcp.tools.pane_tools.io import CAPTURE_DEFAULT_MAX_LINES
from libtmux_mcp.tools.pane_tools.state import (
    _PaneState,
    _raise_if_pane_lifecycle_changed,
    _read_history_limit,
    _read_pane_state,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane


CAPTURE_SINCE_DEFAULT_MAX_LINES = CAPTURE_DEFAULT_MAX_LINES
CAPTURE_SINCE_DEFAULT_MAX_BYTES = 128_000

_CURSOR_PREFIX = "capture-since-v1:"
_CURSOR_VERSION = 1
_STABLE_READ_ATTEMPTS = 3


@dataclass(frozen=True)
class _CaptureCursor:
    """Decoded capture_since cursor payload."""

    pane_id: str
    pane_pid: str
    history_size: int
    pane_height: int
    anchor_abs: int
    anchor_hash: str | None
    below_hashes: tuple[str, ...]


@dataclass(frozen=True)
class _PaneRead:
    """Synchronous tmux read result used by the async tool wrapper."""

    state: _PaneState
    cursor_rows: list[str]
    lines: list[str]
    lines_missed: bool


@dataclass(frozen=True)
class _LimitedLines:
    """Tail-preserved result after line and byte limits are applied."""

    lines: list[str]
    truncated: bool
    truncated_lines: int
    truncated_bytes: int


def _line_hash(line: str) -> str:
    """Return a stable content hash for a tmux row."""
    return hashlib.sha256(line.encode("utf-8", "surrogateescape")).hexdigest()


def _capture_rows(
    pane: Pane,
    *,
    start: t.Literal["-"] | int | None = None,
    end: t.Literal["-"] | int | None = None,
) -> list[str]:
    """Return pane rows as a concrete list."""
    rows = pane.capture_pane(start=start, end=end)
    if rows is None:
        return []
    return list(rows)


def _capture_cursor_rows(pane: Pane, state: _PaneState) -> list[str]:
    """Capture rows from the cursor through the visible bottom."""
    if state.cursor_y >= state.pane_height:
        return []
    return _capture_rows(pane, start=state.cursor_y, end=None)


def _same_state(left: _PaneState, right: _PaneState) -> bool:
    """Return True when two pane snapshots describe the same grid point."""
    return left == right


def _raise_if_dead_without_baseline(pane: Pane, state: _PaneState) -> None:
    """Raise a tool error for a dead pane before a cursor exists."""
    if state.pane_dead:
        msg = f"pane {pane.pane_id} died during pane read"
        raise ToolError(msg)


def _read_stable_visible(
    pane: Pane,
    *,
    baseline_pid: str | None = None,
) -> _PaneRead:
    """Capture the visible pane and cursor rows with a stable state snapshot."""
    for _attempt in range(_STABLE_READ_ATTEMPTS):
        before = _read_pane_state(pane)
        if baseline_pid is None:
            _raise_if_dead_without_baseline(pane, before)
            expected_pid = before.pane_pid
        else:
            expected_pid = baseline_pid
            _raise_if_pane_lifecycle_changed(pane, before, expected_pid)

        lines = _capture_rows(pane)
        cursor_rows = _capture_cursor_rows(pane, before)
        after = _read_pane_state(pane)
        _raise_if_pane_lifecycle_changed(pane, after, expected_pid)
        if _same_state(before, after):
            return _PaneRead(
                state=after,
                cursor_rows=cursor_rows,
                lines=lines,
                lines_missed=False,
            )

    state = _read_pane_state(pane)
    if baseline_pid is None:
        _raise_if_dead_without_baseline(pane, state)
    else:
        _raise_if_pane_lifecycle_changed(pane, state, baseline_pid)
    return _PaneRead(
        state=state,
        cursor_rows=_capture_cursor_rows(pane, state),
        lines=_capture_rows(pane),
        lines_missed=True,
    )


def _cursor_anchor_lost(cursor: _CaptureCursor, state: _PaneState) -> bool:
    """Return True when sampled state proves tmux lost the cursor anchor."""
    bottom_abs = state.history_size + state.pane_height - 1
    if cursor.anchor_abs > bottom_abs:
        return True
    # A complete history wipe (``clear-history``) always destroys the
    # anchor regardless of pane height — the grid is reset to zero.
    if state.history_size == 0 and cursor.history_size > 0:
        return True
    # ``anchor_abs < history_size`` means the anchor has scrolled into
    # retained history, where ``capture-pane -S`` can still address it
    # with a negative start offset.
    #
    # The ``pane_height`` guard distinguishes resize-grow (which pulls
    # rows from history back into the visible region without freeing
    # data) from actual trim (where row data is destroyed).
    return state.history_size < cursor.history_size and (
        state.pane_height <= cursor.pane_height
    )


def _history_limit_trim_risk(
    cursor: _CaptureCursor,
    state: _PaneState,
    history_limit: int,
) -> bool:
    """Return True when tmux may have rebased retained-history rows."""
    if history_limit <= 0:
        return True
    trim_batch = max(history_limit // 10, 1)
    risk_floor = history_limit - trim_batch
    return cursor.history_size >= risk_floor or state.history_size >= risk_floor


def _find_unique_cursor_match(rows: list[str], cursor: _CaptureCursor) -> int | None:
    """Find one retained row sequence matching the cursor fingerprint."""
    if cursor.anchor_hash is None:
        return None

    fingerprint = (cursor.anchor_hash, *cursor.below_hashes)
    if len(rows) < len(fingerprint):
        return None

    match_index: int | None = None
    for index in range(len(rows) - len(fingerprint) + 1):
        candidate = rows[index : index + len(fingerprint)]
        candidate_hashes = tuple(_line_hash(line) for line in candidate)
        if candidate_hashes != fingerprint:
            continue
        if match_index is not None:
            return None
        match_index = index
    return match_index


def _drop_previously_seen_rows(
    rows: list[str],
    cursor: _CaptureCursor,
) -> list[str]:
    """Drop the cursor anchor and below-cursor rows already represented."""
    if not rows:
        return []

    output: list[str] = []
    tail = rows
    if cursor.anchor_hash is not None and _line_hash(rows[0]) == cursor.anchor_hash:
        tail = rows[1:]
    else:
        output.append(rows[0])
        tail = rows[1:]

    drop = 0
    for expected_hash, line in zip(cursor.below_hashes, tail, strict=False):
        if _line_hash(line) != expected_hash:
            break
        drop += 1
    output.extend(tail[drop:])
    return output


def _read_delta(pane: Pane, cursor: _CaptureCursor) -> _PaneRead:
    """Capture rows since ``cursor`` or fall back to visible content on loss."""
    history_limit = _read_history_limit(pane)
    for _attempt in range(_STABLE_READ_ATTEMPTS):
        before = _read_pane_state(pane)
        _raise_if_pane_lifecycle_changed(pane, before, cursor.pane_pid)
        if _cursor_anchor_lost(cursor, before):
            missed = _read_stable_visible(pane, baseline_pid=cursor.pane_pid)
            return _PaneRead(
                state=missed.state,
                cursor_rows=missed.cursor_rows,
                lines=missed.lines,
                lines_missed=True,
            )

        trim_risk = _history_limit_trim_risk(cursor, before, history_limit)
        start = cursor.anchor_abs - before.history_size
        rows = (
            _capture_rows(pane, start="-", end=None)
            if trim_risk
            else (
                []
                if start >= before.pane_height
                else _capture_rows(pane, start=start, end=None)
            )
        )
        cursor_rows = _capture_cursor_rows(pane, before)
        after = _read_pane_state(pane)
        _raise_if_pane_lifecycle_changed(pane, after, cursor.pane_pid)
        if _same_state(before, after):
            if trim_risk:
                match_index = _find_unique_cursor_match(rows, cursor)
                if match_index is None:
                    missed = _read_stable_visible(pane, baseline_pid=cursor.pane_pid)
                    return _PaneRead(
                        state=missed.state,
                        cursor_rows=missed.cursor_rows,
                        lines=missed.lines,
                        lines_missed=True,
                    )
                rows = rows[match_index:]
            return _PaneRead(
                state=after,
                cursor_rows=cursor_rows,
                lines=_drop_previously_seen_rows(rows, cursor),
                lines_missed=False,
            )

    missed = _read_stable_visible(pane, baseline_pid=cursor.pane_pid)
    return _PaneRead(
        state=missed.state,
        cursor_rows=missed.cursor_rows,
        lines=missed.lines,
        lines_missed=True,
    )


def _build_cursor(pane_id: str, state: _PaneState, cursor_rows: list[str]) -> str:
    """Encode the current cursor anchor as an opaque string."""
    payload: dict[str, t.Any] = {
        "version": _CURSOR_VERSION,
        "pane_id": pane_id,
        "pane_pid": state.pane_pid,
        "history_size": state.history_size,
        "pane_height": state.pane_height,
        "anchor_abs": state.history_size + state.cursor_y,
        "anchor_hash": _line_hash(cursor_rows[0]) if cursor_rows else None,
        "below_hashes": [_line_hash(line) for line in cursor_rows[1:]],
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{_CURSOR_PREFIX}{encoded}"


def _raise_invalid_cursor(reason: str) -> t.NoReturn:
    """Raise a consistently worded invalid-cursor error."""
    msg = f"invalid capture_since cursor: {reason}"
    raise ToolError(msg)


def _cursor_str(payload: t.Mapping[str, t.Any], key: str) -> str:
    """Read a required string from a cursor payload."""
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        reason = f"missing or invalid {key}"
        _raise_invalid_cursor(reason)
    return value


def _cursor_int(payload: t.Mapping[str, t.Any], key: str) -> int:
    """Read a required non-negative integer from a cursor payload."""
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        reason = f"missing or invalid {key}"
        _raise_invalid_cursor(reason)
    return value


def _decode_cursor(cursor: str) -> _CaptureCursor:
    """Decode and validate an opaque ``capture_since`` cursor."""
    if not cursor.startswith(_CURSOR_PREFIX):
        reason = "unsupported cursor format"
        _raise_invalid_cursor(reason)
    encoded = cursor.removeprefix(_CURSOR_PREFIX)
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(f"{encoded}{padding}")
        payload: t.Any = json.loads(raw)
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as err:
        reason = "could not decode payload"
        msg = f"invalid capture_since cursor: {reason}"
        raise ToolError(msg) from err

    if not isinstance(payload, dict):
        reason = "payload is not an object"
        _raise_invalid_cursor(reason)
    if payload.get("version") != _CURSOR_VERSION:
        reason = "unsupported cursor version"
        _raise_invalid_cursor(reason)

    anchor_hash_value = payload.get("anchor_hash")
    if anchor_hash_value is not None and not isinstance(anchor_hash_value, str):
        reason = "missing or invalid anchor_hash"
        _raise_invalid_cursor(reason)
    below_hashes_value = payload.get("below_hashes")
    if not isinstance(below_hashes_value, list) or not all(
        isinstance(item, str) for item in below_hashes_value
    ):
        reason = "missing or invalid below_hashes"
        _raise_invalid_cursor(reason)

    return _CaptureCursor(
        pane_id=_cursor_str(payload, "pane_id"),
        pane_pid=_cursor_str(payload, "pane_pid"),
        history_size=_cursor_int(payload, "history_size"),
        pane_height=_cursor_int(payload, "pane_height"),
        anchor_abs=_cursor_int(payload, "anchor_abs"),
        anchor_hash=anchor_hash_value,
        below_hashes=tuple(below_hashes_value),
    )


def _validate_limits(max_lines: int | None, max_bytes: int | None) -> None:
    """Validate caller-supplied truncation limits."""
    if max_lines is not None and max_lines <= 0:
        msg = f"max_lines must be positive or None (received {max_lines})"
        raise ToolError(msg)
    if max_bytes is not None and max_bytes <= 0:
        msg = f"max_bytes must be positive or None (received {max_bytes})"
        raise ToolError(msg)


def _encoded_size(lines: list[str]) -> int:
    """Return UTF-8 byte size for the returned line payload."""
    return len("\n".join(lines).encode("utf-8", "surrogateescape"))


def _limit_lines(
    lines: list[str],
    *,
    max_lines: int | None,
    max_bytes: int | None,
) -> _LimitedLines:
    """Apply tail-preserving line and byte limits."""
    kept = list(lines)
    truncated_lines = 0
    truncated_bytes = 0

    if max_lines is not None and len(kept) > max_lines:
        dropped = kept[:-max_lines]
        kept = kept[-max_lines:]
        truncated_lines += len(dropped)
        truncated_bytes += _encoded_size(dropped)

    if max_bytes is not None:
        while kept and _encoded_size(kept) > max_bytes:
            if len(kept) == 1:
                encoded = kept[0].encode("utf-8", "surrogateescape")
                truncated_bytes += max(len(encoded) - max_bytes, 0)
                kept = [
                    encoded[-max_bytes:].decode("utf-8", "ignore")
                    if max_bytes > 0
                    else ""
                ]
                break
            removed = kept.pop(0)
            truncated_lines += 1
            truncated_bytes += len(f"{removed}\n".encode("utf-8", "surrogateescape"))

    return _LimitedLines(
        lines=kept,
        truncated=truncated_lines > 0 or truncated_bytes > 0,
        truncated_lines=truncated_lines,
        truncated_bytes=truncated_bytes,
    )


@handle_tool_errors_async
async def capture_since(
    cursor: str | None = None,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    max_lines: int | None = CAPTURE_SINCE_DEFAULT_MAX_LINES,
    max_bytes: int | None = CAPTURE_SINCE_DEFAULT_MAX_BYTES,
    socket_name: str | None = None,
) -> CaptureSinceResult:
    """Capture new tmux terminal scrollback since the previous cursor.

    Use for observation-first workflows: tailing a shell, watching a
    long-running command, or repeatedly checking a tmux workspace pane
    without re-sending the same visible screen every turn. The first
    call with ``cursor=None`` returns the current visible pane and an
    opaque cursor. Later calls pass that cursor back and receive only
    rows written or rewritten after the cursor, as long as tmux still
    retains the required scrollback history.

    If tmux history was cleared or trimmed before the cursor anchor,
    the tool returns the current visible pane with ``lines_missed=True``
    and a fresh cursor. Malformed cursors, cursors for a different
    pane, pane death, and pane respawn fail with ``ToolError`` so
    agents do not accidentally observe the wrong process.

    Parameters
    ----------
    cursor : str, optional
        Opaque cursor returned by a prior ``capture_since`` call. When
        omitted, the tool captures the current visible screen and
        starts a new cursor.
    pane_id : str, optional
        Pane ID (e.g. '%1'). Optional when ``cursor`` is supplied; the
        cursor carries the original pane id.
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    max_lines : int or None
        Maximum number of lines to return. Defaults to
        :data:`CAPTURE_SINCE_DEFAULT_MAX_LINES`. Pass ``None`` to
        disable line truncation.
    max_bytes : int or None
        Maximum UTF-8 bytes to return across ``lines``. Defaults to
        :data:`CAPTURE_SINCE_DEFAULT_MAX_BYTES`. Pass ``None`` to
        disable byte truncation.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    CaptureSinceResult
        Structured lines, cursor, elapsed time, and truncation/loss
        metadata.
    """
    _validate_limits(max_lines, max_bytes)
    decoded = _decode_cursor(cursor) if cursor is not None else None
    if decoded is not None and not any(
        value is not None for value in (pane_id, session_name, session_id, window_id)
    ):
        pane_id = decoded.pane_id

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    assert pane.pane_id is not None

    if decoded is not None and pane.pane_id != decoded.pane_id:
        msg = (
            f"cursor pane {decoded.pane_id} does not match requested pane "
            f"{pane.pane_id}"
        )
        raise ToolError(msg)

    start_time = time.monotonic()
    if decoded is None:
        read = await asyncio.to_thread(_read_stable_visible, pane)
    else:
        read = await asyncio.to_thread(_read_delta, pane, decoded)

    limited = _limit_lines(read.lines, max_lines=max_lines, max_bytes=max_bytes)
    elapsed = time.monotonic() - start_time
    return CaptureSinceResult(
        pane_id=pane.pane_id,
        cursor=_build_cursor(pane.pane_id, read.state, read.cursor_rows),
        lines=limited.lines,
        elapsed_seconds=round(elapsed, 3),
        lines_missed=read.lines_missed,
        truncated=limited.truncated,
        truncated_lines=limited.truncated_lines,
        truncated_bytes=limited.truncated_bytes,
    )
