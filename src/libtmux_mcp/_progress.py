"""Progress reporting for the wait tools.

``run_command`` and ``wait_for_channel`` await a single tmux child and
hear nothing until it returns, so without a ticker beside them a client
watching a thirty-second call saw the same thing whether the command
was running or the server had stopped answering.

``wait_for_text`` has a poll loop and could report from inside it, but
does not, and that is deliberate. Reporting per iteration ties the
notification rate to ``interval`` -- a polling knob with a 0.01 floor
-- so the default emitted about twenty notifications a second and the
floor about a hundred, each an awaited JSON-RPC message carrying the
same sentence with a different decimal. The message only changes
meaningfully once a second. All three use this, at one cadence.

The shape is the delicate part. The ticker must not outlive the wait,
must not swallow the cancellation the wait path is careful to
propagate, and must not turn a disconnected client into a failed tool
call.
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
        # Await the cancelled ticker or it is left pending at teardown;
        # ``suppress`` keeps its CancelledError from replacing the
        # caller's.
        with contextlib.suppress(asyncio.CancelledError):
            await task


#: ``ClosedResourceError`` is the send side closing (our shutdown),
#: ``BrokenResourceError`` the receive side (peer disconnect),
#: ``BrokenPipeError`` stdio, ``ConnectionError`` socket families.
#: Anything else propagates so the caller sees it.
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
