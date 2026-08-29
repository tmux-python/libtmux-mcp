"""Bounded waiting / polling tool for pane output."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import typing as t

from fastmcp import Context

# Explicit re-export form: these are part of wait.py's surface as far
# as its tests and their monkeypatches are concerned, and mypy's
# no-implicit-reexport otherwise refuses a test that patches one.
from libtmux_mcp._bounded_io import (
    _bounded_capture as _bounded_capture,  # noqa: PLC0414
    _bounded_history_limit as _bounded_history_limit,  # noqa: PLC0414
    _bounded_pane_state as _bounded_pane_state,  # noqa: PLC0414
    _resolve_pane_bounded as _resolve_pane_bounded,  # noqa: PLC0414
    _run_tmux_lines as _run_tmux_lines,  # noqa: PLC0414
)
from libtmux_mcp._pane_state import (
    _raise_if_pane_lifecycle_changed,
)
from libtmux_mcp._patterns import compile_pattern
from libtmux_mcp._progress import (
    _maybe_log as _maybe_log,  # noqa: PLC0414
    _maybe_report_progress as _maybe_report_progress,  # noqa: PLC0414
    progress_ticker,
)
from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server_async,
    handle_tool_errors_async,
)
from libtmux_mcp._wait_policy import _wait_ceiling_seconds
from libtmux_mcp.models import WaitForTextResult
from libtmux_mcp.tools.pane_tools.capture_since import _limit_lines

logger = logging.getLogger(__name__)


#: Caps on ``WaitForTextResult.tail``. Bounded by BYTES as well as
#: lines because ``capture-pane -J`` joins wrapped rows, so one logical
#: line can be far wider than ``pane_width``.
_TAIL_MAX_LINES = 20
_TAIL_MAX_BYTES = 2_000

#: Mirrors :class:`~libtmux_mcp.models.WaitForTextResult.outcome`.
_WaitOutcome: t.TypeAlias = t.Literal[
    "matched", "any_output", "stopped", "alternate_screen", "timeout"
]


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
        Seconds to sleep between polls. Default 0.05 (50ms). Minimum
        0.01.

        A sleep, not a period: the tick's tmux reads happen first and
        the sleep follows, so the achieved period is ``interval`` plus
        the cost of those reads. Below roughly 0.02 the reads dominate
        and a smaller interval buys proportionally fewer polls than it
        costs in load — asking for 100 polls/s yields about half that.
        Raising it is the cheap knob: a 10s wait costs about 14% of a
        core at the default and 3.3% at 0.25.
    match_case : bool
        Whether to match case. Default False (case-insensitive).
    socket_name : str, optional
        tmux socket name.
    ctx : fastmcp.Context, optional
        FastMCP context; when injected the tool reports progress to the
        client once a second, independently of ``interval``. Omitted in
        tests.

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

    server = await _get_server_async(socket_name=socket_name)

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

    # ``hs0 + cy0`` is the absolute grid anchor, invariant under later
    # scrolling because tmux's ``-S`` is relative to the live ``hsize`` at
    # capture time (cmd-capture-pane.c: ``top = gd->hsize + n``).
    # ``pane_pid`` catches a respawn mid-wait, which would otherwise leave
    # the anchor pointing at the old process's output.
    entry = await _bounded_pane_state(server, target, deadline=deadline)
    baseline_abs = entry.history_size + entry.cursor_y
    baseline_pid = entry.pane_pid
    baseline_hlimit = await _bounded_history_limit(server, target, deadline=deadline)

    # The entry cursor row and everything below it, BY CONTENT. The cursor
    # anchor alone matches any row from start_line on, stale paint (TUI
    # repaints, paste-text) included; filtering each tick against this set
    # makes it an honest "written after entry" predicate.
    #
    # Starts AT ``cursor_y``, not below it: on a quiescent pane the cursor
    # sits at the end of the prompt, so a command's first line lands on
    # that row. Suppressing the row by index instead makes it unmatchable,
    # while content-filtering keeps the prompt text out and the text
    # appended after it in.
    entry_rows = await _bounded_capture(
        server, target, start=entry.cursor_y, deadline=deadline
    )
    # A LIST compared per index, not a set. The ambiguity this resolves is
    # confined to ONE row -- the entry cursor row, which the anchor above
    # deliberately includes so a daemon's single "ready" line stays
    # matchable -- so dedup cannot simply be loosened instead. Flattened, a
    # line twenty rows below the cursor permanently blocks a fresh
    # identical line on the cursor row: a program printing "BUILD OK" a
    # second time is suppressed and the wait runs to its ceiling reporting
    # found=false. Waiting for a repeated status line is the headline case.
    entry_below_cursor: list[str] = list(entry_rows)

    # Scans the WHOLE visible screen, not just the rows the delta filter
    # suppresses: text printed moments ago sits ABOVE the cursor, and a
    # below-cursor scan calls that a clean miss, leaving the agent unable
    # to tell "already there" from "never arrived".
    visible_rows = await _bounded_capture(server, target, start=0, deadline=deadline)

    # Honest, non-heuristic diagnostic: did a success pattern already
    # match a row the delta filter is about to suppress? That is the
    # single most common reason a wait "should have" matched instantly
    # and instead ran to the ceiling.
    stale_at_entry = _first_match(compiled_patterns, visible_rows) is not None
    # Same for ``stop``: an agent re-running a build reads a bare
    # "timeout" as "still running" when a failure marker was already on
    # screen. A separate field, because success text and failure text
    # predating the call call for opposite reactions.
    stop_stale_at_entry = _first_match(compiled_stop, visible_rows) is not None

    matched_lines: list[str] = []
    outcome: _WaitOutcome = "timeout"
    matched_index: int | None = None
    saw_new_output = False
    warned_risk_band = False
    saw_alternate_screen = entry.alternate_on
    last_rows: list[str] = []

    # Its own 1 s cadence, not one per poll: per-iteration progress ties
    # the notification rate to ``interval``, whose 0.01 floor means ~100
    # awaited JSON-RPC messages a second carrying the same sentence with a
    # different decimal. The message only changes once a second.
    #
    # Spend it on the numbers rather than restating the pane. Clients
    # usually surface the message over the raw progress/total pair, and it
    # is likeliest to survive a future transport.
    try:
        async with progress_ticker(
            ctx,
            total=effective_timeout,
            message=lambda elapsed, left: (
                f"Waiting on pane {target}: {elapsed:.1f}s elapsed, {left:.1f}s left"
            ),
        ):
            while True:
                # FastMCP direct-awaits async tools on the main event loop
                # and the tmux reads are blocking subprocess calls. Push
                # them to the default executor so concurrent tool calls are
                # not starved during long waits.
                state = await _bounded_pane_state(server, target, deadline=deadline)
                _raise_if_pane_lifecycle_changed(target, state, baseline_pid)
                if state.alternate_on:
                    saw_alternate_screen = True
                # At ``history-limit``, ``grid_collect_history`` (grid.c)
                # frees the oldest rows and decrements ``gd->hsize``, so
                # absolute index math on ``history_size + cursor_y`` is no
                # longer recoverable. ``clear-history`` decrements it too.
                #
                # So does resize-grow with ``hscrolled > 0``, where
                # ``screen_resize_y`` (screen.c) moves rows from history
                # into the visible region and frees nothing. ``pane_height``
                # separates them -- trim leaves it unchanged, resize-grow
                # increases it -- so the conjunction below is the real
                # eviction signature.
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
                # The shrink guard above misses grid_collect_history trim
                # during continuous output, where hsize bounces between
                # (hlimit - hlimit/10) and hlimit faster than we can poll.
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
                # ON the entry cursor row, not below it: that row is where
                # the next line lands on a quiescent pane, so skipping it by
                # index makes a daemon's single ``ready`` line unmatchable.
                # ``entry_below_cursor`` suppresses what the row already
                # held without hiding what arrives on it.
                start_line = baseline_abs - state.history_size
                # ``capture-pane -S`` clips a below-visible start back to
                # the bottom row (cmd-capture-pane.c:205-206), so a naive
                # capture returns stale bottom-row text until new rows
                # appear. ``pane_height`` is re-read each tick so a mid-wait
                # resize cannot leave the guard keyed to a stale height.
                if start_line >= state.pane_height:
                    rows: list[str] = []
                else:
                    rows = await _bounded_capture(
                        server, target, start=start_line, deadline=deadline
                    )
                last_rows = rows
                # A row is new when it differs from what THAT index held at
                # entry; rows past the entry capture are new by
                # construction. Residual and not fixable from tmux
                # primitives: a row rewriting the same text at the same
                # index still reads as unchanged, since tmux exposes no
                # per-row write time. This shrinks the hole from "any line
                # below the cursor" to "the exact row that held that text".
                new_lines = [
                    line
                    for index, line in enumerate(rows)
                    if index >= len(entry_below_cursor)
                    or line != entry_below_cursor[index]
                ]
                if new_lines:
                    saw_new_output = True

                if state.alternate_on:
                    # A full-screen program repaints the whole grid, so
                    # rows "below the cursor" are its paint and matching
                    # them is a false accept -- worse than waiting. Never
                    # latches, so quitting a pager mid-wait resumes.
                    if time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(interval)
                    continue

                stop_hit = _first_match(compiled_stop, new_lines)
                pattern_hit = _first_match(compiled_patterns, new_lines)
                # ``stop`` wins a same-tick tie. Each tick re-captures the
                # whole region, so a failure at t=1.00 and a success at
                # t=1.02 arrive in the SAME ``new_lines``, and letting
                # ``patterns`` win lets a broad success pattern swallow
                # every failure marker the caller passed.
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
                    # the wait. Its own outcome, so an agent that dropped
                    # ``patterns`` can SEE it matched "something moved",
                    # not "the thing I wanted".
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
