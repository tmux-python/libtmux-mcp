"""Waiting / polling tools for pane content changes."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time

from fastmcp import Context
from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _get_server,
    _resolve_pane,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    ContentChangeResult,
    WaitForTextResult,
)


async def _maybe_report_progress(
    ctx: Context | None,
    *,
    progress: float,
    total: float | None,
    message: str,
) -> None:
    """Call ``ctx.report_progress`` if a Context is available.

    Tests call the wait tools with ``ctx=None`` so progress plumbing is
    optional. Failures from ``report_progress`` (e.g. client has closed
    the connection) are suppressed because a progress report must never
    be able to take down a tool call.
    """
    if ctx is None:
        return
    with contextlib.suppress(Exception):
        await ctx.report_progress(progress=progress, total=total, message=message)


@handle_tool_errors_async
async def wait_for_text(
    pattern: str,
    regex: bool = False,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    timeout: float = 8.0,
    interval: float = 0.05,
    match_case: bool = False,
    content_start: int | None = None,
    content_end: int | None = None,
    socket_name: str | None = None,
    ctx: Context | None = None,
) -> WaitForTextResult:
    """Wait for text to appear in a tmux pane.

    Polls the pane content at regular intervals until the pattern is found
    or the timeout is reached. Use this instead of polling capture_pane
    manually — it saves agent tokens and turns.

    When a :class:`fastmcp.Context` is available, this tool emits
    periodic ``ctx.report_progress`` notifications so MCP clients can
    show a "polling pane X... (elapsed/timeout)" indicator during long
    waits. Progress notifications never block the timeout contract —
    if the client connection is gone the progress call is suppressed
    and polling continues.

    Parameters
    ----------
    pattern : str
        Text to wait for. Treated as literal text by default. Set
        ``regex=True`` to interpret as a regular expression.
    regex : bool
        Whether to interpret pattern as a regular expression. Default False
        (literal text matching).
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    timeout : float
        Maximum seconds to wait. Default 8.0.
    interval : float
        Seconds between polls. Default 0.05 (50ms).
    match_case : bool
        Whether to match case. Default False (case-insensitive).
    content_start : int, optional
        Start line for capture. Negative values reach into scrollback.
    content_end : int, optional
        End line for capture.
    socket_name : str, optional
        tmux socket name.
    ctx : fastmcp.Context, optional
        FastMCP context; when injected the tool reports progress to the
        client. Omitted in tests.

    Returns
    -------
    WaitForTextResult
        Result with found status, matched lines, and timing info.
    """
    search_pattern = pattern if regex else re.escape(pattern)
    flags = 0 if match_case else re.IGNORECASE
    try:
        compiled = re.compile(search_pattern, flags)
    except re.error as e:
        msg = f"Invalid regex pattern: {e}"
        raise ToolError(msg) from e

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    assert pane.pane_id is not None
    matched_lines: list[str] = []
    start_time = time.monotonic()
    deadline = start_time + timeout
    found = False

    while True:
        elapsed = time.monotonic() - start_time
        await _maybe_report_progress(
            ctx,
            progress=elapsed,
            total=timeout,
            message=f"Polling pane {pane.pane_id} for pattern",
        )

        lines = pane.capture_pane(start=content_start, end=content_end)
        hits = [line for line in lines if compiled.search(line)]
        if hits:
            matched_lines.extend(hits)
            found = True
            break

        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(interval)

    elapsed = time.monotonic() - start_time
    return WaitForTextResult(
        found=found,
        matched_lines=matched_lines,
        pane_id=pane.pane_id,
        elapsed_seconds=round(elapsed, 3),
        timed_out=not found,
    )


@handle_tool_errors_async
async def wait_for_content_change(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    timeout: float = 8.0,
    interval: float = 0.05,
    socket_name: str | None = None,
    ctx: Context | None = None,
) -> ContentChangeResult:
    """Wait for any content change in a tmux pane.

    Captures the current pane content, then polls until the content differs
    or the timeout is reached. Use this after send_keys when you don't know
    what the output will be — it waits for "something happened" rather than
    a specific pattern.

    Emits :meth:`fastmcp.Context.report_progress` each tick when a
    Context is injected, so clients can render a progress indicator
    during the wait.

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
    timeout : float
        Maximum seconds to wait. Default 8.0.
    interval : float
        Seconds between polls. Default 0.05 (50ms).
    socket_name : str, optional
        tmux socket name.
    ctx : fastmcp.Context, optional
        FastMCP context for progress notifications. Omitted in tests.

    Returns
    -------
    ContentChangeResult
        Result with changed status and timing info.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    assert pane.pane_id is not None
    initial_content = pane.capture_pane()
    start_time = time.monotonic()
    deadline = start_time + timeout
    changed = False

    while True:
        elapsed = time.monotonic() - start_time
        await _maybe_report_progress(
            ctx,
            progress=elapsed,
            total=timeout,
            message=f"Watching pane {pane.pane_id} for change",
        )

        current = pane.capture_pane()
        if current != initial_content:
            changed = True
            break

        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(interval)

    elapsed = time.monotonic() - start_time
    return ContentChangeResult(
        changed=changed,
        pane_id=pane.pane_id,
        elapsed_seconds=round(elapsed, 3),
        timed_out=not changed,
    )
