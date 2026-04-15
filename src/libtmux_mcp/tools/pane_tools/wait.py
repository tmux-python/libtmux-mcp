"""Waiting / polling tools for pane content changes."""

from __future__ import annotations

import asyncio
import re
import time

import anyio
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

#: Exceptions that indicate "client transport is gone, keep polling".
#: Narrowly-scoped on purpose: a broader ``Exception`` catch would
#: mask real programming errors (``TypeError`` on a renamed kwarg,
#: ``AttributeError`` if ``ctx`` is wired wrong) behind a silent no-op.
#: Both anyio stream errors must be caught: ``ClosedResourceError`` is
#: raised when the *send* side of the stream is closed (our own
#: shutdown path); ``BrokenResourceError`` is raised when the *receive*
#: side is closed (peer disconnect) — FastMCP's own client catches
#: both for the same reason. ``BrokenPipeError`` covers stdio
#: transports; generic ``ConnectionError`` is the catch-all base for
#: socket-level families. Anything else propagates so the caller
#: sees it.
_TRANSPORT_CLOSED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    BrokenPipeError,
    ConnectionError,
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
    optional. Only transport-closed exceptions are suppressed — a
    progress report that fails because the client has disconnected is
    unsurprising and must not take down the tool call. Everything else
    (programming errors, kwarg mismatches, FastMCP internal failures)
    propagates so it shows up in logs and tests instead of being
    silently swallowed.
    """
    if ctx is None:
        return
    try:
        await ctx.report_progress(progress=progress, total=total, message=message)
    except _TRANSPORT_CLOSED_EXCEPTIONS:
        # Client gone; the poll loop will either complete or hit its
        # timeout and return normally. No progress notification leaks.
        return


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

    Notes
    -----
    **Safety tier.** Tagged ``readonly`` because the tool observes
    pane state without mutating it. Readonly clients may therefore
    block for the caller-supplied ``timeout`` (default 8 s, caller
    may pass larger values). The capture call runs on the asyncio
    default thread-pool executor, whose size caps concurrent waits
    (``min(32, os.cpu_count() + 4)`` on CPython); a malicious
    readonly client could saturate that pool with long-timeout
    calls. If you need to rate-limit wait tools, do it at the
    transport layer or with dedicated middleware.
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

        # FastMCP direct-awaits async tools on the main event loop; the
        # libtmux capture_pane call is a blocking subprocess.run. Push
        # to the default executor so concurrent tool calls are not
        # starved during long waits.
        lines = await asyncio.to_thread(
            pane.capture_pane, start=content_start, end=content_end
        )
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

    Notes
    -----
    **Safety tier.** Tagged ``readonly`` because the tool observes
    pane state without mutating it. Readonly clients may therefore
    block for the caller-supplied ``timeout`` (default 8 s, caller
    may pass larger values). The capture call runs on the asyncio
    default thread-pool executor, whose size caps concurrent waits
    (``min(32, os.cpu_count() + 4)`` on CPython); a malicious
    readonly client could saturate that pool with long-timeout
    calls. If you need to rate-limit wait tools, do it at the
    transport layer or with dedicated middleware.
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
    # See comment in wait_for_text: push the blocking capture off the
    # main event loop via asyncio.to_thread.
    initial_content = await asyncio.to_thread(pane.capture_pane)
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

        current = await asyncio.to_thread(pane.capture_pane)
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
