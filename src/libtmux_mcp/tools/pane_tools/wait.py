"""Bounded waiting / polling tool for pane output."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import typing as t

import anyio
from fastmcp import Context
from libtmux import exc

from libtmux_mcp._patterns import compile_pattern
from libtmux_mcp._tmux_proc import _run_tmux_bounded
from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _tmux_argv,
    handle_tool_errors_async,
)
from libtmux_mcp._wait_policy import _wait_ceiling_seconds
from libtmux_mcp.models import WaitForTextResult
from libtmux_mcp.tools.pane_tools.capture_since import _limit_lines
from libtmux_mcp.tools.pane_tools.state import (
    HISTORY_LIMIT_FORMAT,
    PANE_STATE_FORMAT,
    _PaneState,
    _parse_pane_state,
    _raise_if_pane_lifecycle_changed,
)

if t.TYPE_CHECKING:
    from libtmux.server import Server

logger = logging.getLogger(__name__)

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

#: Caps on ``WaitForTextResult.tail``. Bounded by BYTES as well as
#: lines because ``capture-pane -J`` joins wrapped rows, so one logical
#: line can be far wider than ``pane_width``.
_TAIL_MAX_LINES = 20
_TAIL_MAX_BYTES = 2_000

#: Mirrors :class:`~libtmux_mcp.models.WaitForTextResult.outcome`.
_WaitOutcome = t.Literal[
    "matched", "any_output", "stopped", "alternate_screen", "timeout"
]


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


# ---------------------------------------------------------------------------
# Timeout-bounded tmux reads
# ---------------------------------------------------------------------------


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
    return await _first_pane_of_window(server, windows[0], deadline=deadline)


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
    return rows[0]


async def _first_pane_of_window(
    server: Server, window_id: str, *, deadline: float | None
) -> str:
    """Return the window's FIRST listed pane, matching ``panes[0]``.

    Deliberately not the active pane: ``_resolve_pane`` indexes the
    list, and swapping in ``#{?pane_active,...}`` would change which
    pane an existing caller gets.
    """
    rows = await _run_tmux_lines(
        server, "list-panes", "-t", window_id, "-F", "#{pane_id}", deadline=deadline
    )
    if not rows:
        raise exc.PaneNotFound
    return rows[0]


# ---------------------------------------------------------------------------
# Pattern compilation
# ---------------------------------------------------------------------------


async def _compile_patterns(
    values: list[str],
    *,
    label: str,
    regex: bool,
    match_case: bool,
    ctx: Context | None,
) -> list[re.Pattern[str]]:
    """Compile one pattern list, raising ``ExpectedToolError`` on bad input."""
    flags = 0 if match_case else re.IGNORECASE
    compiled: list[re.Pattern[str]] = []
    for value in values:
        if not value:
            msg = f"{label} pattern must be a non-empty string"
            raise ExpectedToolError(msg)
        try:
            compiled.append(
                compile_pattern(value, regex=regex, flags=flags, label=label)
            )
        except ExpectedToolError as e:
            await _maybe_log(ctx, level="warning", message=str(e))
            raise
    return compiled


def _first_match(
    compiled: list[re.Pattern[str]], lines: list[str]
) -> tuple[int, list[str]] | None:
    """Return ``(pattern_index, matching_lines)`` for the first pattern that hits."""
    for index, pattern in enumerate(compiled):
        hits = [line for line in lines if pattern.search(line)]
        if hits:
            return index, hits
    return None


@handle_tool_errors_async
async def wait_for_text(
    patterns: list[str] | None = None,
    stop: list[str] | None = None,
    regex: bool = False,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    timeout: float = 8.0,
    interval: float = 0.05,
    match_case: bool = False,
    socket_name: str | None = None,
    ctx: Context | None = None,
) -> WaitForTextResult:
    r"""Wait for NEW output in a tmux pane, then return.

    Polls until one of ``patterns`` appears on a line written *after*
    this call starts, one of ``stop`` appears (immediate failure exit),
    or the timeout expires. Pass ``patterns=null`` to wait for any new
    output at all. Use this instead of polling ``capture_pane`` in a
    loop.

    Pre-existing scrollback is never matched, and neither is paint left
    below the cursor at entry — only rows written after the call began
    count. If a pattern was already on screen the result says so via
    ``matched_at_entry``.

    **Last resort: reserve for output you did not author.** Commands
    you send are AUTHORED — use ``run_command`` (returns exit status)
    or compose ``; tmux wait-for -S <channel>`` with ``wait_for_channel``
    instead, both cheaper and exact. For unattributable recurring
    prompts or background log lines, bracket your own command with a
    unique sentinel (``cmd; echo __WAIT_$RANDOM__``) and wait for that.

    ``stop`` is the cheap way to avoid burning the whole budget: pass
    the failure markers you already know (``"error:"``, ``"FAILED"``,
    ``"Traceback"``) and a failed run returns in milliseconds instead
    of at the ceiling.

    The server caps ``timeout``. An over-large value is not an error —
    the wait returns at the ceiling and reports ``effective_timeout``.

    Parameters
    ----------
    patterns : list of str, optional
        Success patterns; the first one to match ends the wait.
        Literal text unless ``regex=True``. Omit or pass ``null`` to
        wait for any new output.
    stop : list of str, optional
        Failure patterns. A hit ends the wait immediately with
        ``outcome="stopped"`` and ``found=false``; ``matched_index``
        says which entry fired.
    regex : bool
        Interpret ``patterns`` and ``stop`` as regular expressions.
        Default False (literal text).
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    timeout : float
        Requested seconds to wait. Default 8.0. Clamped by server
        policy; see ``effective_timeout`` in the result.
    interval : float
        Seconds between polls. Default 0.05 (50ms). Minimum 0.01.
    match_case : bool
        Whether to match case. Default False (case-insensitive).
    socket_name : str, optional
        tmux socket name.
    ctx : fastmcp.Context, optional
        FastMCP context; when injected the tool reports progress to the
        client. Omitted in tests.

    Returns
    -------
    WaitForTextResult
        Match outcome, a bounded tail of what the pane printed, and the
        timeout actually enforced.

    Notes
    -----
    **Matching happens in Python, never in tmux.** Patterns are never
    interpolated into a tmux format string: tmux's format parser treats
    ``#`` and ``}`` structurally, so an ordinary regex quantifier
    corrupts field parsing and a pattern ending in ``#`` swallows the
    rest of the format. Only fixed literal formats reach tmux.

    **Every tmux call is timeout-bounded.** Reads are spawned with
    ``asyncio.create_subprocess_exec`` and bounded against the wait's
    own deadline, rather than going through libtmux's untimed
    ``Popen.communicate()``. Nothing in this path runs on a worker
    thread, so a wedged tmux server can neither pin the event loop nor
    hang interpreter shutdown.

    **Alternate screen / pagers suppress matching.** Inside ``less`` or
    any full-screen program, ``capture-pane`` returns the program's
    painted rows, so matching them would report text the program had
    already drawn. Matching is skipped for as long as the alternate
    screen lasts and the result comes back as ``alternate_screen``
    rather than ``timeout`` — read the screen, don't retry the wait.

    **Scrollback rollover detection is partial.** The tool raises when
    ``hsize`` shrinks below the entry value (``clear-history``, and any
    rollover whose dip is observable between polls). It does not
    reliably detect ``grid_collect_history`` trim during continuous
    output; a runtime ``ctx.warning`` fires when sampled state enters
    the trim-risk band. Use ``wait_for_channel`` when correctness
    matters more than convenience.
    """
    ceiling = _wait_ceiling_seconds()

    if interval < 0.01:
        msg = f"interval must be at least 0.01 s (received {interval})"
        raise ExpectedToolError(msg)
    if timeout <= 0:
        msg = f"timeout must be positive (received {timeout})"
        raise ExpectedToolError(msg)
    if patterns is not None and not patterns:
        msg = "patterns must be a non-empty list, or null to wait for any new output"
        raise ExpectedToolError(msg)

    effective_timeout = min(timeout, ceiling)
    # No ``timeout_clamped`` flag: it is exactly
    # ``effective_timeout < what_you_passed``, which the caller can
    # compute, and a field the agent never branches on is permanent
    # weight in ``outputSchema``.

    compiled_patterns = await _compile_patterns(
        patterns or [],
        label="patterns",
        regex=regex,
        match_case=match_case,
        ctx=ctx,
    )
    compiled_stop = await _compile_patterns(
        stop or [],
        label="stop",
        regex=regex,
        match_case=match_case,
        ctx=ctx,
    )

    server = _get_server(socket_name=socket_name)

    # Anchor ``start_time`` before pane resolution: that call reaches
    # tmux too, so leaving it outside the clock hid it from
    # ``elapsed_seconds`` as well as from the deadline.
    start_time = time.monotonic()
    deadline = start_time + effective_timeout

    target = await _resolve_pane_bounded(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
        deadline=deadline,
    )

    # Snapshot the pane state before polling. ``hs0 + cy0`` is the
    # absolute grid anchor — invariant under subsequent scrolling
    # because tmux's ``-S`` is relative to the live ``hsize`` at
    # capture time (cmd-capture-pane.c: ``top = gd->hsize + n``).
    # ``pane_pid`` lets us detect a respawn-pane mid-wait that would
    # otherwise leave the absolute anchor pointing at the old
    # process's output. See issue #45.
    entry = await _bounded_pane_state(server, target, deadline=deadline)
    baseline_abs = entry.history_size + entry.cursor_y
    baseline_pid = entry.pane_pid
    baseline_hlimit = await _bounded_history_limit(server, target, deadline=deadline)

    # Snapshot the entry cursor row and everything below it, BY CONTENT.
    # The cursor anchor alone matches any row at start_line onward, which
    # includes stale paint-style content (TUI repaints, paste-text, manual
    # cursor positioning) that pre-dates the wait. Filtering per-tick
    # captures against this set turns the cursor anchor into an honest
    # "content written after entry" predicate.
    #
    # The capture starts AT ``cursor_y``, not below it. Suppressing the
    # entry row by index instead was a shipped false negative: on a
    # quiescent pane the cursor sits at the end of the prompt, so the
    # first line a command prints lands on that very row and was never
    # matchable. Content-filtering covers the same stale-paint case
    # without the blind spot, because the prompt text that was on the row
    # at entry is in this set while text appended to it afterwards is not.
    entry_rows = await _bounded_capture(
        server, target, start=entry.cursor_y, deadline=deadline
    )
    # Kept as a LIST, compared per index. A set discards position, and
    # the ambiguity this resolves is confined to one row -- the entry
    # cursor row, which the anchor deliberately includes so a daemon's
    # single "ready" line stays matchable. Flattened, a line twenty rows
    # below the cursor permanently blocked a fresh identical line
    # arriving on the cursor row: a program printing "BUILD OK" again,
    # two seconds into the wait, was suppressed and the wait ran to its
    # ceiling reporting found=false. Waiting for a repeated status line
    # is this tool's headline case.
    entry_below_cursor: list[str] = list(entry_rows)

    # ``matched_at_entry`` scans the WHOLE visible screen, not just the
    # rows the delta filter suppresses. The usual shape of this mistake
    # is text a command printed moments ago sitting ABOVE the cursor,
    # which a below-cursor scan reports as a clean miss — the agent
    # then cannot tell "already there" from "never arrived".
    visible_rows = await _bounded_capture(server, target, start=0, deadline=deadline)

    # Honest, non-heuristic diagnostic: did a success pattern already
    # match a row the delta filter is about to suppress? That is the
    # single most common reason a wait "should have" matched instantly
    # and instead ran to the ceiling.
    stale_at_entry = _first_match(compiled_patterns, visible_rows) is not None
    # Same rationale applied to ``stop``. A failure marker already on
    # screen is the case where a bare "timeout" misleads most: an agent
    # re-running a build reads it as "still running" when the honest
    # answer is "the previous run already failed". Kept a separate field
    # because "my success text predates the call" and "my failure text
    # predates the call" call for opposite reactions.
    stop_stale_at_entry = _first_match(compiled_stop, visible_rows) is not None

    matched_lines: list[str] = []
    outcome: _WaitOutcome = "timeout"
    matched_index: int | None = None
    saw_new_output = False
    warned_risk_band = False
    saw_alternate_screen = entry.alternate_on
    last_rows: list[str] = []

    try:
        while True:
            elapsed = time.monotonic() - start_time
            # Spend the message on the numbers, not on restating the
            # pane. A constant string here is a wasted channel: clients
            # that surface progress at all usually show the message and
            # not the raw progress/total pair, and "how much budget is
            # left" is the only thing a human watching a long wait wants
            # to know. It is also the field most likely to survive a
            # future transport — MCP background tasks drop numeric
            # progress entirely and keep only a status message.
            await _maybe_report_progress(
                ctx,
                progress=elapsed,
                total=effective_timeout,
                message=(
                    f"Waiting on pane {target}: {elapsed:.1f}s elapsed, "
                    f"{max(effective_timeout - elapsed, 0.0):.1f}s left"
                ),
            )

            # FastMCP direct-awaits async tools on the main event loop
            # and the tmux reads are blocking subprocess calls. Push
            # them to the default executor so concurrent tool calls are
            # not starved during long waits.
            state = await _bounded_pane_state(server, target, deadline=deadline)
            _raise_if_pane_lifecycle_changed(target, state, baseline_pid)
            if state.alternate_on:
                saw_alternate_screen = True
            # When tmux's ``history-limit`` is reached, ``grid_collect_history``
            # (grid.c) frees the oldest scrollback rows and decrements
            # ``gd->hsize``, so absolute index math anchored on
            # ``history_size + cursor_y`` is no longer recoverable. The same
            # hsize-decrement also fires on ``clear-history``.
            #
            # ``hsize`` ALSO decrements on resize-grow when ``hscrolled > 0``
            # (``screen.c`` ``screen_resize_y``: rows are pulled from history
            # back into the visible region). In that case no row data is freed
            # — only the hsize/visible-region partition shifts and absolute
            # indices stay valid. Trim and resize-grow are distinguished by
            # ``pane_height``: trim leaves it unchanged, resize-grow increases
            # it. The conjunction below is the actual signature of row
            # eviction; resize-grow falls through cleanly.
            if (
                state.history_size < entry.history_size
                and state.pane_height <= entry.pane_height
            ):
                msg = (
                    f"pane {target} history shrank below entry "
                    f"baseline (history_size {entry.history_size} -> "
                    f"{state.history_size}); baseline anchor lost — "
                    "re-arm wait_for_text or use wait_for_channel for "
                    "deterministic synchronization"
                )
                raise ExpectedToolError(msg)
            # The shrink guard above catches clear-history and the
            # entry-at-cap rollover edge. It does NOT catch
            # grid_collect_history trim during continuous output, where
            # hsize bounces between (hlimit - hlimit/10) and hlimit
            # faster than we can poll. Emit a one-shot warning when
            # sampled state is in the trim-risk band.
            if not warned_risk_band and baseline_hlimit > 0:
                trim_batch = max(baseline_hlimit // 10, 1)
                risk_floor = baseline_hlimit - trim_batch
                if state.history_size >= risk_floor:
                    await _maybe_log(
                        ctx,
                        level="warning",
                        message=(
                            f"pane {target} is polling in the "
                            "history-limit trim-risk band "
                            f"(history_size {state.history_size} / "
                            f"history_limit {baseline_hlimit}); "
                            "wait_for_text correctness is best-effort "
                            "here. For deterministic synchronization "
                            "use wait_for_channel."
                        ),
                    )
                    warned_risk_band = True
            # Anchored ON the entry cursor row, not below it. That row is
            # where the next line lands on a quiescent pane, so skipping
            # it by index made the tool's headline case — a daemon
            # printing one ``ready`` line — structurally unmatchable. The
            # ``entry_below_cursor`` content filter below suppresses what
            # was already on the row without hiding what arrives on it.
            start_line = baseline_abs - state.history_size
            # ``capture-pane -S`` clips a below-visible start back to the
            # bottom row (cmd-capture-pane.c, post-tmux-3.0), so a naive
            # capture would return stale bottom-row text whenever no new rows
            # have appeared below the cursor yet. Compare against
            # ``state.pane_height`` (re-read each tick) so a resize mid-wait
            # doesn't leave the guard keyed to a stale height.
            if start_line >= state.pane_height:
                rows: list[str] = []
            else:
                rows = await _bounded_capture(
                    server, target, start=start_line, deadline=deadline
                )
            last_rows = rows
            # Drop lines whose content was already below the entry
            # cursor — stale paint, not output written after the call.
            # A row is new when it differs from what THAT index held at
            # entry. Rows past the entry capture are new by construction.
            # Residual, and not fixable from tmux primitives: a row that
            # rewrites the same text at the same index still reads as
            # unchanged. tmux exposes no per-row last-written time, so
            # something content-shaped is unavoidable at the bottom --
            # this shrinks the hole from "any line anywhere below the
            # cursor" to "the exact row that already held that text".
            new_lines = [
                line
                for index, line in enumerate(rows)
                if index >= len(entry_below_cursor) or line != entry_below_cursor[index]
            ]
            if new_lines:
                saw_new_output = True

            if state.alternate_on:
                # A full-screen program owns and repaints the whole
                # grid, so rows "below the cursor" are its paint, not
                # output written after this call. Matching them reports
                # text the program had already drawn — a false accept,
                # which is worse than waiting. Skip matching for as long
                # as it lasts; never latch, so quitting a pager mid-wait
                # resumes an honest wait.
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(interval)
                continue

            stop_hit = _first_match(compiled_stop, new_lines)
            pattern_hit = _first_match(compiled_patterns, new_lines)
            # ``stop`` wins a same-tick tie. Every tick re-captures the
            # whole region, so a failure line at t=1.00 and a success
            # line at t=1.02 arrive in the SAME ``new_lines`` — letting
            # ``patterns`` win there means a broad success pattern (a
            # shell-prompt regex, say) silently swallows every failure
            # marker the caller supplied, which defeats the entire
            # point of passing ``stop``.
            if stop_hit is not None:
                matched_index, matched_lines = stop_hit
                outcome = "stopped"
                break
            if pattern_hit is not None:
                matched_index, matched_lines = pattern_hit
                outcome = "matched"
                break
            if not compiled_patterns and new_lines:
                # ``patterns=None`` catch-all: any new output satisfies
                # the wait. Subsumes the former wait_for_content_change.
                # Reported as its own outcome so an agent that dropped
                # ``patterns`` under context pressure can SEE that it
                # matched "something moved", not "the thing I wanted".
                matched_lines = _limit_lines(
                    list(new_lines),
                    max_lines=_TAIL_MAX_LINES,
                    max_bytes=_TAIL_MAX_BYTES,
                ).lines
                outcome = "any_output"
                break

            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # MCP cancellation: client hung up or aborted the request.
        # Re-raise so fastmcp's transport layer can complete shutdown
        # — never return a partial WaitForTextResult, which would mask
        # the cancellation as a timed-out wait.
        logger.debug(
            "wait_for_text cancelled after %.3fs on pane %s",
            time.monotonic() - start_time,
            target,
        )
        raise

    elapsed = time.monotonic() - start_time
    found = outcome in {"matched", "any_output"}
    if outcome == "timeout" and saw_alternate_screen:
        # Reclassify: "timeout" tells an agent its PATTERN was wrong.
        # Here the pane spent the wait under a full-screen program, so
        # matching was suppressed and the tool never got to look. That
        # is a different fix — read the screen, don't retry the wait.
        outcome = "alternate_screen"
    if not found:
        await _maybe_log(
            ctx,
            level="warning",
            message=f"No match in pane {target} before {effective_timeout}s timeout",
        )

    limited_tail = _limit_lines(
        last_rows, max_lines=_TAIL_MAX_LINES, max_bytes=_TAIL_MAX_BYTES
    )
    limited_matches = _limit_lines(
        matched_lines, max_lines=_TAIL_MAX_LINES, max_bytes=_TAIL_MAX_BYTES
    )
    return WaitForTextResult(
        found=found,
        outcome=outcome,
        matched_index=matched_index,
        matched_lines=limited_matches.lines,
        saw_new_output=saw_new_output,
        matched_at_entry=stale_at_entry and not found,
        stop_matched_at_entry=stop_stale_at_entry,
        alternate_screen=saw_alternate_screen,
        tail=limited_tail.lines,
        pane_id=target,
        elapsed_seconds=round(elapsed, 3),
        effective_timeout=effective_timeout,
    )
