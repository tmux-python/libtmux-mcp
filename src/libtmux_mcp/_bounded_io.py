"""Wall-clock-bounded tmux reads, shared by the async tools.

Split out of ``wait.py`` so ``capture_since`` can use the same reads
without an import cycle -- ``wait.py`` imports ``_limit_lines`` from
there. Every function here goes through
:func:`libtmux_mcp._tmux_proc._run_tmux_bounded`, which owns a killable
subprocess rather than a worker thread: a thread blocked in libtmux's
untimed ``Popen.communicate()`` cannot be cancelled, and
``concurrent.futures.thread._python_exit`` joins pool workers untimed at
shutdown, so one wedged tmux takes process exit with it.
"""

from __future__ import annotations

import time
import typing as t

from libtmux_mcp._tmux_proc import _run_tmux_bounded
from libtmux_mcp._utils import ExpectedToolError, _tmux_argv

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from libtmux.server import Server

    from libtmux_mcp.tools.pane_tools.state import _PaneState


#: Per-``tmux``-invocation wall-clock bound.
#:
#: This is the load-bearing half of the wait ceiling. libtmux runs tmux
#: through ``Popen.communicate()`` with no timeout, and
#: ``mcp.tool(timeout=...)`` bounds only the coroutine (it uses
#: ``anyio.fail_after``), so neither bounds the actual work. The wait
#: path therefore spawns tmux itself, as an async subprocess it can
#: kill. It must not use a thread for this: a worker stuck in
#: ``Popen.communicate()`` cannot be cancelled, and
#: ``concurrent.futures.thread._python_exit`` joins every pool worker
#: untimed at interpreter shutdown — measured, one wedged tmux hangs
#: process exit and Ctrl-C forever, after a 300 s pause and a
#: ``RuntimeWarning`` from ``shutdown_default_executor``.
#:
#: This is the CEILING on a single call; :func:`_call_budget` lowers it
#: to whatever remains of the caller's own deadline, so the wait cannot
#: overshoot by a whole call's worth.
_TMUX_CALL_TIMEOUT_SECONDS = 5.0
#: Floor for a budget-derived per-call timeout. Without it, a wait
#: whose deadline has just passed would hand ``subprocess.run`` a
#: non-positive timeout and raise instantly, reporting "tmux is
#: unresponsive" for what is really a normal expiry.
_TMUX_CALL_MIN_SECONDS = 0.25


def _call_budget(deadline: float | None) -> float:
    """Return the per-call tmux timeout, never overshooting ``deadline``.

    A fixed 5 s cap lets a single wedged call run past the caller's
    own deadline, and the poll loop issues two reads per tick with the
    deadline check only at the end — so a fixed cap makes the true
    worst case ``effective_timeout + 2 x 5 s``, not
    ``effective_timeout``. Deriving each call's timeout from the
    remaining budget collapses that back: the wait cannot exceed its
    deadline by more than the floor below.

    The floor keeps a nearly-exhausted budget from passing a zero or
    negative timeout to the per-call bound, which would fire
    immediately and turn a normal expiry into a spurious "tmux is
    unresponsive" error.
    """
    if deadline is None:
        return _TMUX_CALL_TIMEOUT_SECONDS
    remaining = deadline - time.monotonic()
    return max(min(_TMUX_CALL_TIMEOUT_SECONDS, remaining), _TMUX_CALL_MIN_SECONDS)


async def _run_tmux_lines(
    server: Server, *args: str, deadline: float | None = None
) -> list[str]:
    """Run one tmux subcommand under a hard wall-clock bound.

    Returns stdout split on newlines with trailing blanks stripped,
    matching :class:`libtmux.common.tmux_cmd`'s own normalisation so
    call sites see the same shape they did when they went through
    libtmux.

    ``deadline`` is a :func:`time.monotonic` reading; when given, the
    subprocess timeout is bounded by the budget remaining until it.

    The spawn itself lives in :func:`~libtmux_mcp._tmux_proc._run_tmux_bounded`,
    shared with ``wait_for_channel``; see that module for why this path
    owns an async subprocess instead of a worker thread.
    """
    argv = _tmux_argv(server, *args)
    budget = _call_budget(deadline)
    try:
        returncode, stdout, stderr = await _run_tmux_bounded(argv, timeout=budget)
    except TimeoutError as e:
        msg = (
            f"tmux {args[0]} did not return within "
            f"{budget:.2f}s; the tmux server is unresponsive"
        )
        raise ExpectedToolError(msg) from e
    if returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        msg = f"tmux {args[0]} failed: {detail or f'exit {returncode}'}"
        raise ExpectedToolError(msg)
    out = stdout.decode("utf-8", errors="backslashreplace").split("\n")
    while out and out[-1] == "":
        out.pop()
    return out


async def _bounded_pane_state(
    server: Server, pane_id: str, *, deadline: float | None = None
) -> _PaneState:
    """Read :class:`_PaneState` bounded by the remaining wait budget."""
    from libtmux_mcp.tools.pane_tools.state import (
        PANE_STATE_FORMAT,
        _parse_pane_state,
    )

    out = await _run_tmux_lines(
        server,
        "display-message",
        "-p",
        "-t",
        pane_id,
        PANE_STATE_FORMAT,
        deadline=deadline,
    )
    return _parse_pane_state(out[0] if out else "0|0|0||0|0")


async def _bounded_history_limit(
    server: Server, pane_id: str, *, deadline: float | None = None
) -> int:
    """Read ``history-limit`` bounded by the remaining wait budget."""
    from libtmux_mcp.tools.pane_tools.state import HISTORY_LIMIT_FORMAT

    out = await _run_tmux_lines(
        server,
        "display-message",
        "-p",
        "-t",
        pane_id,
        HISTORY_LIMIT_FORMAT,
        deadline=deadline,
    )
    return int(out[0]) if out and out[0].isdigit() else 0


async def _bounded_capture(
    server: Server, pane_id: str, *, start: int, deadline: float | None = None
) -> list[str]:
    """Capture pane rows from ``start`` under a hard timeout.

    ``-J`` joins tmux's visual wraps so a pattern spanning the wrap
    column still matches one logical line. ``-p`` prints to stdout.
    No caller-supplied text reaches this argv.
    """
    return await _run_tmux_lines(
        server,
        "capture-pane",
        "-p",
        "-J",
        "-t",
        pane_id,
        "-S",
        str(start),
        deadline=deadline,
    )
