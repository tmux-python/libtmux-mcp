"""Bounded tmux reads, shared across the tool modules.

Two different bounds live here. The wall-clock one keeps a single tmux
invocation killable; the SIZE one (:func:`_truncate_lines_tail` and
:data:`CAPTURE_DEFAULT_MAX_LINES`) keeps a large result from blowing the
agent's context window. The size half moved here from
``pane_tools/io.py`` because ``buffer_tools`` needed it too, and a tool
module importing from a sibling tool module made the package
import-order-dependent -- adding ``copy_selection``, which needs the
buffer helpers, closed that into a genuine cycle.

Split out of ``wait.py`` so ``capture_since`` can use the same reads
without an import cycle -- ``wait.py`` imports ``_limit_lines`` from
there. Every function here goes through
:func:`libtmux_mcp._tmux_proc._run_tmux_bounded`, which owns a killable
subprocess rather than a worker thread: a thread blocked in libtmux's
untimed ``Popen.communicate()`` cannot be cancelled, and
``concurrent.futures.thread._python_exit`` joins pool workers untimed at
shutdown, so one wedged tmux takes process exit with it.

Building argv here rather than going through libtmux means the flags
are ours to get right, and CI runs tmux 3.2a upward. Check a new one
against the oldest supported version before using it -- the arg string
in tmux's own source is authoritative::

    git show 3.2a:cmd-capture-pane.c | grep -m1 'args = {'
"""

from __future__ import annotations

import time
import typing as t

from libtmux import exc

from libtmux_mcp._tmux_proc import _run_tmux_bounded
from libtmux_mcp._utils import (
    _LIVENESS_TIMEOUT_SECONDS,
    ExpectedToolError,
    _tmux_argv,
    tmux_id_sort_key,
)

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
_TMUX_CALL_TIMEOUT_SECONDS = _LIVENESS_TIMEOUT_SECONDS
#: Floor for a budget-derived per-call timeout. Without it, a wait
#: whose deadline has just passed would hand ``subprocess.run`` a
#: non-positive timeout and raise instantly, reporting "tmux is
#: unresponsive" for what is really a normal expiry.
_TMUX_CALL_MIN_SECONDS = 0.25


#: Default line cap applied to :func:`capture_pane` and similar scrollback
#: readers. Large enough to cover typical prompt + a few screens of output,
#: small enough that a pathological pane (e.g. 50K lines of ``tail -f``)
#: cannot blow the agent's context window on a single call. Callers who
#: need a full capture can pass ``max_lines=None`` to opt out.
CAPTURE_DEFAULT_MAX_LINES = 500


def _truncate_lines_tail(
    lines: list[str], max_lines: int | None
) -> tuple[list[str], bool, int]:
    """Return the tail of ``lines`` at most ``max_lines`` long.

    Tail-preserving truncation is required for terminal output: the
    most recent lines (active prompt, latest command output) live at
    the bottom of the scrollback buffer. Dropping the head keeps what
    the agent actually needs.

    Parameters
    ----------
    lines : list of str
        The captured lines, oldest first.
    max_lines : int or None
        Maximum number of lines to keep. ``None`` disables truncation.

    Returns
    -------
    tuple
        ``(kept, truncated, dropped)`` — the kept suffix, whether
        truncation happened, and how many lines were dropped.

    Examples
    --------
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=2)
    (['b', 'c'], True, 1)
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=5)
    (['a', 'b', 'c'], False, 0)
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=None)
    (['a', 'b', 'c'], False, 0)
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=0)
    Traceback (most recent call last):
    libtmux_mcp._utils.ExpectedToolError: max_lines must be at least 1, ...
    """
    if max_lines is not None and max_lines < 1:
        # Python slices a non-positive cap into nonsense rather than
        # failing: ``lines[-0:]`` is the WHOLE list, so max_lines=0
        # returned more rows than no truncation at all while announcing
        # that everything had been dropped, and a negative inflated the
        # count past the pane's own size -- 112 truncated from 12.
        # The header is this tool's only disclosure channel, so a number
        # that cannot be true is the whole defect.
        msg = (
            f"max_lines must be at least 1, or null for no limit (received {max_lines})"
        )
        raise ExpectedToolError(msg)
    if max_lines is None or len(lines) <= max_lines:
        return lines, False, 0
    dropped = len(lines) - max_lines
    return lines[-max_lines:], True, dropped


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


async def _resolve_pane_bounded(
    server: Server,
    *,
    pane_id: str | None,
    session_name: str | None,
    session_id: str | None,
    window_id: str | None,
    deadline: float | None = None,
) -> str:
    """Resolve a pane target natively, without libtmux and without threads.

    libtmux's resolvers are synchronous and reach tmux through
    ``Popen.communicate()`` with no timeout. Calling one bare from an
    ``async def`` freezes the whole event loop; calling it through
    ``asyncio.to_thread`` frees the loop but parks a pool worker that
    cannot be cancelled — and
    ``concurrent.futures.thread._python_exit`` joins every pool worker
    untimed at interpreter shutdown, so a single wedged tmux hangs
    process exit and Ctrl-C forever. Neither arrangement is fixable
    while a thread is involved, so this reproduces the resolution
    against :func:`_run_tmux_lines`, which owns a killable subprocess.

    Mirrors :func:`libtmux_mcp._utils._resolve_pane` for exactly the
    four targeting arguments this tool accepts, including which
    argument wins and which exception each miss raises, so the
    agent-visible error text is unchanged.
    """
    # 1. ``pane_id`` short-circuits everything else.
    if pane_id is not None:
        rows = await _run_tmux_lines(
            server, "list-panes", "-a", "-F", "#{pane_id}", deadline=deadline
        )
        if pane_id not in rows:
            raise exc.PaneNotFound(pane_id=pane_id)
        return pane_id

    # 2. ``window_id`` short-circuits session resolution.
    if window_id is not None:
        rows = await _run_tmux_lines(
            server, "list-windows", "-a", "-F", "#{window_id}", deadline=deadline
        )
        matches = [row for row in rows if row == window_id]
        if not matches:
            raise exc.TmuxObjectDoesNotExist(
                obj_key="window_id",
                obj_id=window_id,
                list_cmd="list-windows",
                list_extra_args=("-a",),
            )
        if len(matches) > 1:
            # ``list-windows -a`` emits a window once per session it is
            # linked into, so a unique id can still match twice. libtmux
            # raises here rather than guessing, and so must we — silently
            # picking the first would be a behaviour change.
            raise exc.MultipleObjectsReturned(
                count=len(matches), query={"window_id": window_id}
            )
        return await _first_pane_of_window(server, window_id, deadline=deadline)

    # 3. ``session_id`` wins over ``session_name``; with neither, the
    #    first listed session is used.
    target_session = await _resolve_session_native(
        server, session_name=session_name, session_id=session_id, deadline=deadline
    )
    windows = await _run_tmux_lines(
        server,
        "list-windows",
        "-t",
        target_session,
        "-F",
        "#{window_id}",
        deadline=deadline,
    )
    if not windows:
        raise exc.NoWindowsExist
    return await _first_pane_of_window(
        server, min(windows, key=tmux_id_sort_key), deadline=deadline
    )


async def _resolve_session_native(
    server: Server,
    *,
    session_name: str | None,
    session_id: str | None,
    deadline: float | None,
) -> str:
    """Return a session id, mirroring ``_resolve_session``'s precedence."""
    if session_id is not None:
        rows = await _run_tmux_lines(
            server, "list-sessions", "-F", "#{session_id}", deadline=deadline
        )
        if session_id not in rows:
            raise exc.TmuxObjectDoesNotExist(
                obj_key="session_id",
                obj_id=session_id,
                list_cmd="list-sessions",
                list_extra_args=(),
            )
        return session_id
    if session_name is not None:
        rows = await _run_tmux_lines(
            server,
            "list-sessions",
            "-F",
            "#{session_name}\t#{session_id}",
            deadline=deadline,
        )
        for row in rows:
            name, _, sid = row.partition("\t")
            if name == session_name:
                return sid
        raise exc.TmuxObjectDoesNotExist(
            obj_key="session_name",
            obj_id=session_name,
            list_cmd="list-sessions",
            list_extra_args=(),
        )
    rows = await _run_tmux_lines(
        server, "list-sessions", "-F", "#{session_id}", deadline=deadline
    )
    if not rows:
        raise exc.TmuxObjectDoesNotExist(
            obj_key="session",
            obj_id="(any)",
            list_cmd="list-sessions",
            list_extra_args=(),
        )
    return min(rows, key=tmux_id_sort_key)


async def _first_pane_of_window(
    server: Server, window_id: str, *, deadline: float | None
) -> str:
    """Return the window's oldest pane, matching ``_resolve_pane``.

    Deliberately not the active pane: the canonical resolver keys on
    the immutable id so two untargeted calls agree, and focus is
    something any client can move between them.
    """
    rows = await _run_tmux_lines(
        server, "list-panes", "-t", window_id, "-F", "#{pane_id}", deadline=deadline
    )
    if not rows:
        raise exc.PaneNotFound
    return min(rows, key=tmux_id_sort_key)


# ---------------------------------------------------------------------------
# Pattern compilation
# ---------------------------------------------------------------------------
