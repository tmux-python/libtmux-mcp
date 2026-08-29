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

Every async tool now reaches tmux this way -- ``wait_for_text``,
``wait_for_channel``, ``capture_since`` and ``run_command``. The last
two were converted after a socket that forwards its FIRST connection
and stalls the rest showed them unable to return AND the process unable
to exit; a socket that never answers cannot show it, because the
bounded liveness probe catches that one before the unbounded call is
reached. The event loop keeps ticking throughout, so no loop-blocking
test can see this class either.

``asyncio.to_thread`` remains correct for BOUNDED work -- see
``_run_send_keys``, whose every argv runs under a timeout, so its worker
always returns. The hazard is the untimed call, not the thread.

``tests/test_pane_tools.py`` enforces this structurally: it reads the
tree for a tmux call made inline from an async body, and for a libtmux
method called on any receiver. Both halves of the failure -- the call
never returning, and the process then unable to exit -- were confirmed
by two independently built fixtures, each shown to fire when the defect
is present and to stay silent when it is not.
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
    # Not ``wait_for(proc.communicate())``: a wedged tmux can leave a
    # grandchild holding the pipe write ends, so ``proc.wait()`` after
    # ``kill()`` deadlocks. ``asyncio.wait`` kills first, cancels after.
    task = asyncio.ensure_future(proc.communicate())
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if not done:
            await _kill_and_reap(proc, task)
            raise TimeoutError
        stdout, stderr = task.result()
    except asyncio.CancelledError:
        # Reap before propagating, or tmux is orphaned. The guard spans
        # both await points: a cancel lands on ``asyncio.wait`` as often
        # as on ``task.result()``.
        await _kill_and_reap(proc, task)
        raise
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr
