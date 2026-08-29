"""Cancellable, wall-clock-bounded ``tmux`` invocation.

Every tool that blocks for a caller-chosen duration needs the same two
guarantees from its ``tmux`` child: it must not outlive its budget, and
it must not outlive a cancelled call. Neither libtmux's ``tmux_cmd``
(``Popen.communicate()`` with no timeout) nor
``asyncio.to_thread(subprocess.run, ..., timeout=...)`` provides the
second one — ``to_thread`` hands the coroutine a ``CancelledError``
immediately while the worker thread stays blocked in the untimed
``waitpid``, so the child runs on for the rest of its budget with
nobody waiting for it. Measured on ``tmux wait-for``: a 15 s wait
cancelled at 2 s left the child alive for another 13 s.

A worker thread is also unrecoverable at shutdown:
``concurrent.futures.thread._python_exit`` — registered through
``threading._register_atexit`` — joins every pool worker with no
timeout, so one wedged tmux hangs interpreter exit forever, and no
thread-based arrangement avoids it (a private pool with
``shutdown(wait=False)`` is joined by that same hook).

A subprocess we own can simply be killed, so this module owns it.

``wait_for_text`` and ``wait_for_channel`` are converted. ``capture_since``
and ``run_command`` are NOT: their tmux reads still go through
``asyncio.to_thread``, so a tmux server that answers once and then stops
answering leaves those two calls unable to return and the process unable
to exit. Measured with a socket that forwards the first connection and
stalls the rest -- the event loop keeps ticking, so a loop-blocking test
cannot see it. Named here rather than left as a principle, because
"neither arrangement is fixable while a thread is involved" reads as
settled policy and invites the inference that the tree already complies
everywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import typing as t

#: Bound on the post-kill reap. Short because it is best effort: the
#: loop's child watcher reaps the pid whether or not we wait.
_TMUX_REAP_SECONDS = 0.5


async def _kill_and_reap(
    proc: asyncio.subprocess.Process, task: asyncio.Future[t.Any]
) -> None:
    """Kill a tmux child and tear down its reader without deadlocking.

    Order matters. Killing first lets the reader's cancellation
    actually complete; cancelling first leaves a live process whose
    pipes may be held open by a grandchild, and the reader then never
    finishes. The final reap is bounded for the same reason —
    ``proc.wait()`` can block indefinitely when something other than
    the process we killed still holds the write ends. The event loop's
    child watcher reaps the pid regardless, so a timeout here leaks
    nothing.

    Parameters
    ----------
    proc : asyncio.subprocess.Process
        The tmux child to kill.
    task : asyncio.Future
        The in-flight ``proc.communicate()`` future reading its pipes.
    """
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=_TMUX_REAP_SECONDS)


async def _run_tmux_bounded(
    argv: list[str], *, timeout: float
) -> tuple[int, bytes, bytes]:
    """Run one tmux argv under a hard bound, killing it on cancellation.

    Parameters
    ----------
    argv : list of str
        Full tmux command vector, as built by
        :func:`~libtmux_mcp._utils._tmux_argv`.
    timeout : float
        Wall-clock bound in seconds. On expiry the child is killed and
        reaped before ``TimeoutError`` is raised.

    Returns
    -------
    tuple of (int, bytes, bytes)
        ``(returncode, stdout, stderr)``.

    Raises
    ------
    TimeoutError
        When ``timeout`` elapses first.
    asyncio.CancelledError
        Re-raised after the child has been killed and reaped, so a
        cancelled call never orphans tmux.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Deliberately NOT ``wait_for(proc.communicate())``. Measured: a
    # wedged tmux that leaves a grandchild holding the stdout/stderr
    # write ends never reaches EOF, and ``await proc.wait()`` after
    # ``kill()`` then deadlocks — the pipes outlive the process we
    # killed. ``asyncio.wait`` observes the timeout without cancelling,
    # so the kill happens first and the read task is torn down after,
    # when cancelling it can actually succeed.
    task = asyncio.ensure_future(proc.communicate())
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if not done:
            await _kill_and_reap(proc, task)
            raise TimeoutError
        stdout, stderr = task.result()
    except asyncio.CancelledError:
        # The whole call was cancelled (MCP client hung up). Tear the
        # child down before letting the cancellation through, or tmux
        # is orphaned. The cancellation lands on the ``asyncio.wait``
        # above just as often as on ``task.result()``, so the guard
        # must span both — otherwise a cancel while waiting orphans the
        # child.
        await _kill_and_reap(proc, task)
        raise
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr
