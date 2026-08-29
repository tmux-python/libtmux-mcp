"""Progress reporting for waits that block without a poll loop.

``wait_for_text`` polls, so it reports progress from inside its own
loop. ``run_command`` and ``wait_for_channel`` do not: each awaits one
``tmux wait-for`` child and hears nothing until it returns. A client
watching a thirty-second ``run_command`` therefore saw the same thing
whether the command was running or the server had stopped answering.

A ticker beside the wait closes that, and the shape is the delicate
part. The ticker must not outlive the wait, must not swallow the
cancellation the wait path is careful to propagate, and must not turn a
disconnected client into a failed tool call.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import typing as t

import anyio

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import AsyncIterator

    from fastmcp import Context

#: Seconds between reports. Fine enough that a human sees the number
#: move, coarse enough that a long wait does not flood the transport.
_TICK_SECONDS = 1.0


@contextlib.asynccontextmanager
async def progress_ticker(
    ctx: Context | None,
    *,
    total: float,
    message: t.Callable[[float, float], str],
) -> AsyncIterator[None]:
    """Report elapsed/remaining beside a wait that cannot report itself.

    ``message`` receives ``(elapsed, remaining)`` so callers phrase the
    line themselves; clients that surface progress usually show the
    message rather than the raw pair.

    A ``None`` context starts nothing at all, which is what tests and
    direct Python callers get.
    """
    if ctx is None:
        yield
        return

    started = time.monotonic()

    async def _tick() -> None:
        while True:
            await asyncio.sleep(_TICK_SECONDS)
            elapsed = time.monotonic() - started
            await _maybe_report_progress(
                ctx,
                progress=elapsed,
                total=total,
                message=message(elapsed, max(total - elapsed, 0.0)),
            )

    task = asyncio.create_task(_tick())
    try:
        yield
    finally:
        task.cancel()
        # Awaiting the cancelled task is what makes this safe to use
        # inside a wait that is itself being cancelled: without it the
        # ticker is left pending and asyncio complains at teardown.
        # ``suppress`` keeps the ticker's own CancelledError from
        # replacing the one the caller is propagating.
        with contextlib.suppress(asyncio.CancelledError):
            await task


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


_LogLevel = t.Literal["debug", "info", "warning", "error"]


async def _maybe_log(
    ctx: Context | None,
    *,
    level: _LogLevel,
    message: str,
) -> None:
    """Call the matching ``ctx.{level}`` if a Context is available.

    Sibling to :func:`_maybe_report_progress` for client-visible log
    notifications (``notifications/message`` in MCP). Same suppression
    contract: silent only when the transport is gone, propagating
    everything else so programming errors stay loud.
    """
    if ctx is None:
        return
    method = getattr(ctx, level)
    try:
        await method(message)
    except _TRANSPORT_CLOSED_EXCEPTIONS:
        return
