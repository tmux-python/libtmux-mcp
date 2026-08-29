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

    from libtmux_mcp.tools.pane_tools.wait import _maybe_report_progress

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
