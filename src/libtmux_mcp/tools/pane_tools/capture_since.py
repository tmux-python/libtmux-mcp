"""Incremental capture tool for tmux pane observation.

The cursor machinery — anchor arithmetic, trim-risk re-anchoring, the
stable double-read, and the serialized cursor format — lives in libtmux
as :meth:`~libtmux.pane.Pane.capture_since`. What remains here is the
part that is an MCP concern rather than a tmux one: bounding the
response so a single observation cannot blow an agent's context window.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from libtmux.capture import CaptureCursor

from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    handle_tool_errors_async,
)
from libtmux_mcp.models import CaptureSinceResult
from libtmux_mcp.tools.pane_tools.io import CAPTURE_DEFAULT_MAX_LINES

CAPTURE_SINCE_DEFAULT_MAX_LINES = CAPTURE_DEFAULT_MAX_LINES
CAPTURE_SINCE_DEFAULT_MAX_BYTES = 128_000


@dataclass(frozen=True)
class _LimitedLines:
    """Tail-preserved result after line and byte limits are applied."""

    lines: list[str]
    truncated: bool
    truncated_lines: int
    truncated_bytes: int


def _validate_limits(max_lines: int | None, max_bytes: int | None) -> None:
    """Validate caller-supplied truncation limits."""
    if max_lines is not None and max_lines <= 0:
        msg = f"max_lines must be positive or None (received {max_lines})"
        raise ExpectedToolError(msg)
    if max_bytes is not None and max_bytes <= 0:
        msg = f"max_bytes must be positive or None (received {max_bytes})"
        raise ExpectedToolError(msg)


def _encoded_size(lines: list[str]) -> int:
    """Return UTF-8 byte size for the returned line payload."""
    return len("\n".join(lines).encode("utf-8", "surrogateescape"))


def _limit_lines(
    lines: list[str],
    *,
    max_lines: int | None,
    max_bytes: int | None,
) -> _LimitedLines:
    """Apply tail-preserving line and byte limits.

    Runs after the capture completes and never feeds back into the
    cursor, which libtmux builds from pane state rather than from these
    rows. Truncating a response therefore cannot shift where the next
    observation resumes.
    """
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
    pane, pane death, and pane respawn fail with ``ExpectedToolError`` so
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
        ``CAPTURE_SINCE_DEFAULT_MAX_LINES``. Pass ``None`` to
        disable line truncation.
    max_bytes : int or None
        Maximum UTF-8 bytes to return across ``lines``. Defaults to
        ``CAPTURE_SINCE_DEFAULT_MAX_BYTES``. Pass ``None`` to
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
    decoded = CaptureCursor.from_str(cursor) if cursor is not None else None
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

    start_time = time.monotonic()
    # Off the event loop: every tmux round-trip inside capture_since is a
    # blocking subprocess call, and a stable read makes several.
    read = await asyncio.to_thread(pane.capture_since, decoded)
    limited = _limit_lines(read.lines, max_lines=max_lines, max_bytes=max_bytes)
    elapsed = time.monotonic() - start_time
    return CaptureSinceResult(
        pane_id=pane.pane_id,
        cursor=str(read.cursor),
        lines=limited.lines,
        elapsed_seconds=round(elapsed, 3),
        lines_missed=read.lines_missed,
        truncated=limited.truncated,
        truncated_lines=limited.truncated_lines,
        truncated_bytes=limited.truncated_bytes,
    )
