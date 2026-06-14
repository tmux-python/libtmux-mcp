"""Waiting / polling tools for pane content changes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import typing as t

import anyio
from fastmcp import Context

from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    ContentChangeResult,
    WaitForTextResult,
)
from libtmux_mcp.tools.pane_tools.state import (
    _raise_if_pane_lifecycle_changed,
    _read_history_limit,
    _read_pane_state,
)

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
    socket_name: str | None = None,
    ctx: Context | None = None,
) -> WaitForTextResult:
    r"""Wait for NEW text to appear in a tmux pane.

    Polls the pane at regular intervals until ``pattern`` appears on a
    line written *after* the call starts, or the timeout is reached.
    Use this instead of polling :func:`capture_pane` manually — it
    saves agent tokens and turns.

    **What "new" means.** At entry the tool snapshots two things: the
    pane's absolute grid position (``history_size + cursor_y``) and the
    contents of every row below the entry cursor. Each tick captures
    the rows below the original baseline and discards any row whose
    content matches the entry snapshot — those rows are stale paint
    that pre-dates the wait, not output written after it. Scrollback
    that was already present when the call began is ignored, and so
    is paint-style content left below the cursor by TUI repaints,
    ``paste-text``, or manual cursor positioning. For the synchronous
    "is the pattern in the pane right now?" check, call
    {tooliconl}`search-panes` instead.

    The content-delta filter has a rare false-negative case: if new
    output happens to byte-match a row in the entry snapshot, that
    new row is filtered out. The patterns agents typically wait on
    (command-specific markers, full status strings) make this
    collision unlikely in practice. For stricter "any change"
    semantics, use {tooliconl}`wait-for-content-change`.

    In-place updates to the entry cursor's row — carriage-return
    rewrites, progress spinners, single-line status updates — are
    not observed; only rows below the entry cursor count as "new."
    Use {tooliconl}`wait-for-content-change` or pair the command
    with a sentinel for those cases.

    **Adversarial-safety pattern.** If you cannot trust that the
    pattern only appears after your action — for example because the
    pane prints recurring prompts, log lines, or output from background
    processes you do not control — bracket your command with a unique
    sentinel: ``cmd; echo __WAIT_$RANDOM__`` and wait for the sentinel
    instead of ``cmd``'s natural output. tmux's grid model cannot
    distinguish "your output" from "theirs"; the sentinel can.

    **When NOT to use this — sequential ``send_keys`` race.** If you
    call ``send_keys`` and immediately ``wait_for_text``, fast output
    (``echo``, prompt-return after ``^C``) can land *before* this tool
    snapshots the baseline, and the match is then invisible to the
    wait. The race is small but real on CI and over remote sockets.
    For commands you author, prefer ``run_command`` so completion,
    exit status, and output arrive as one typed result. For custom
    shell composition outside that shape, append
    ``; tmux wait-for -S <channel>`` to your ``send_keys`` payload and
    call ``wait_for_channel`` instead. Reserve ``wait_for_text`` for
    output you do not control
    (third-party process logs, daemon prompts, interactive
    supervisors).

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
    **Scrollback rollover detection is partial.** The tool raises
    ``ExpectedToolError`` when ``hsize`` shrinks below the entry value — which
    catches ``clear-history`` and any rollover where the dip is
    observable between polls. It does **not** reliably detect
    ``grid_collect_history`` trim that fires during continuous output:
    tmux trims (~10% of ``history-limit``) then immediately scrolls
    new lines back, so sampled ``hsize`` can stay clamped at the cap
    and never appear below entry. For deterministic command-completion
    synchronization use ``wait_for_channel``; for observation flows
    that approach ``history-limit``, the tool emits a runtime
    ``ctx.warning`` notification when sampled state enters the
    trim-risk band.

    Note that ``hsize`` also decrements on resize-grow when there is
    scrolled history available (``screen.c`` ``screen_resize_y``),
    but in that case the row data is not freed — only the
    history/visible-region boundary moves and absolute indices stay
    valid. The guard distinguishes the two cases by also requiring
    ``pane_height`` to not have grown, so resize-grow continues
    polling cleanly.

    **Wrapped lines are joined for matching.** Captures pass tmux's
    ``-J`` flag so a pattern that spans the pane's visual wrap is
    still matched against the joined logical line. The returned
    ``matched_lines`` entry for such a hit is the joined line and
    can therefore be longer than ``pane_width``.

    **In-place rewrites below the baseline.** Programs that paint
    over rows the tool will capture — cursor-position escape
    sequences, full-screen progress displays, anything that rewrites
    rows it already wrote — can re-introduce text the caller saw
    earlier. Each tick captures the current contents of rows below
    the baseline; tmux's grid model cannot distinguish "fresh write"
    from "repaint with the same characters."
    ``screen_write_reverseindex`` (``screen-write.c``) only scrolls
    the visible region within ``[rupper, rlower]`` and never touches
    ``hsize``, so ``\\eM`` itself does not invalidate the anchor —
    but the surrounding TUI render loop may. Full-screen TUIs
    typically run on the alternate screen (a separate grid that
    this tool does not traverse), so the main-screen pattern is
    rare in practice.

    **``clear`` / ``reset``.** With the default ``scroll-on-clear``
    option, cleared content scrolls into history (``screen-write.c``
    ``screen_write_clearscreen``), so the baseline anchor is
    unaffected.

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
    if not pattern:
        msg = "pattern must be a non-empty string"
        raise ExpectedToolError(msg)
    if interval < 0.01:
        msg = f"interval must be at least 0.01 s (received {interval})"
        raise ExpectedToolError(msg)
    if timeout <= 0:
        msg = f"timeout must be positive (received {timeout})"
        raise ExpectedToolError(msg)

    search_pattern = pattern if regex else re.escape(pattern)
    flags = 0 if match_case else re.IGNORECASE
    try:
        compiled = re.compile(search_pattern, flags)
    except re.error as e:
        msg = f"Invalid regex pattern: {e}"
        await _maybe_log(ctx, level="warning", message=msg)
        raise ExpectedToolError(msg) from e

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    assert pane.pane_id is not None

    # Anchor ``start_time`` before the baseline read so the elapsed
    # time returned in ``WaitForTextResult.elapsed_seconds`` reflects
    # total call duration, including the baseline read. The
    # user-supplied ``timeout`` still cannot bound a stalled tmux
    # command — libtmux's ``tmux_cmd`` uses ``Popen.communicate()``
    # with no subprocess timeout, so a hung tmux read can exceed the
    # budget. The early anchor measures that blowout; it doesn't
    # prevent it.
    start_time = time.monotonic()
    deadline = start_time + timeout

    # Snapshot the pane state before polling. ``hs0 + cy0`` is the
    # absolute grid anchor — invariant under subsequent scrolling
    # because tmux's ``-S`` is relative to the live ``hsize`` at
    # capture time (cmd-capture-pane.c: ``top = gd->hsize + n``).
    # ``pane_pid`` lets us detect a respawn-pane mid-wait that would
    # otherwise leave the absolute anchor pointing at the old
    # process's output. See issue #45.
    entry = await asyncio.to_thread(_read_pane_state, pane)
    baseline_abs = entry.history_size + entry.cursor_y
    baseline_pid = entry.pane_pid
    baseline_hlimit = await asyncio.to_thread(_read_history_limit, pane)

    # Snapshot rows below the entry cursor by content. The cursor anchor
    # alone matches any row at start_line onward, which includes stale
    # paint-style content (TUI repaints, paste-text, manual cursor
    # positioning) that pre-dates the wait. Filtering per-tick captures
    # against this set turns the cursor anchor into an honest "content
    # written after entry" predicate. Stored as a frozenset for O(1)
    # lookup against the typically small below-cursor row set.
    entry_below_cursor: frozenset[str] = frozenset(
        await asyncio.to_thread(
            pane.capture_pane,
            start=entry.cursor_y + 1,
            end=None,
            join_wrapped=True,
        )
    )

    matched_lines: list[str] = []
    found = False
    warned_risk_band = False

    try:
        while True:
            elapsed = time.monotonic() - start_time
            await _maybe_report_progress(
                ctx,
                progress=elapsed,
                total=timeout,
                message=f"Polling pane {pane.pane_id} for pattern",
            )

            # FastMCP direct-awaits async tools on the main event loop;
            # the libtmux display-message + capture_pane calls are both
            # blocking subprocess.run. Push to the default executor so
            # concurrent tool calls are not starved during long waits.
            state = await asyncio.to_thread(_read_pane_state, pane)
            _raise_if_pane_lifecycle_changed(pane, state, baseline_pid)
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
                    f"pane {pane.pane_id} history shrank below entry "
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
            # sampled state is in the trim-risk band so agents
            # subscribed to MCP log notifications know to verify
            # results or switch to wait_for_channel.
            if not warned_risk_band and baseline_hlimit > 0:
                trim_batch = max(baseline_hlimit // 10, 1)
                risk_floor = baseline_hlimit - trim_batch
                if state.history_size >= risk_floor:
                    await _maybe_log(
                        ctx,
                        level="warning",
                        message=(
                            f"pane {pane.pane_id} is polling in the "
                            "history-limit trim-risk band "
                            f"(history_size {state.history_size} / "
                            f"history_limit {baseline_hlimit}); "
                            "wait_for_text correctness is best-effort "
                            "here. For deterministic synchronization "
                            "use wait_for_channel."
                        ),
                    )
                    warned_risk_band = True
            # ``+ 1`` skips the baseline line itself so we don't
            # re-match the row the cursor sat on at entry.
            start_line = baseline_abs - state.history_size + 1
            # ``capture-pane -S`` clips a below-visible start back to the
            # bottom row (cmd-capture-pane.c, post-tmux-3.0), so a naive
            # capture would return stale bottom-row text whenever no new rows
            # have appeared below the cursor yet. Compare against
            # ``state.pane_height`` (re-read each tick) so a resize mid-wait
            # doesn't leave the guard keyed to a stale height.
            if start_line >= state.pane_height:
                lines: list[str] = []
            else:
                # ``join_wrapped=True`` adds tmux's ``-J`` so visually
                # wrapped lines are returned as one logical line. Without
                # this, a pattern that spans tmux's wrap column is split
                # across two rows and ``re.search`` against each row in
                # isolation never matches. Trade-off: the returned
                # ``matched_lines`` can contain a single string longer
                # than ``pane_width``.
                lines = await asyncio.to_thread(
                    pane.capture_pane,
                    start=start_line,
                    end=None,
                    join_wrapped=True,
                )
            # Filter out lines whose content was already below the
            # entry cursor — those are stale paint, not output written
            # after the call began. Then run the regex against the
            # truly-new lines.
            new_lines = [line for line in lines if line not in entry_below_cursor]
            hits = [line for line in new_lines if compiled.search(line)]
            if hits:
                matched_lines.extend(hits)
                found = True
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
            pane.pane_id,
        )
        raise

    elapsed = time.monotonic() - start_time
    if not found:
        await _maybe_log(
            ctx,
            level="warning",
            message=(
                f"Pattern not found in pane {pane.pane_id} before {timeout}s timeout"
            ),
        )
    return WaitForTextResult(
        found=found,
        matched_lines=matched_lines,
        pane_id=pane.pane_id,
        elapsed_seconds=round(elapsed, 3),
        risk_band_warned=warned_risk_band,
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

    Raises ``ExpectedToolError`` when pane respawn or pane death invalidates the
    baseline captured at entry. For correctness-sensitive flows prefer
    ``wait_for_channel`` composed with ``tmux wait-for -S``.

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
    entry = await asyncio.to_thread(_read_pane_state, pane)
    baseline_pid = entry.pane_pid
    _raise_if_pane_lifecycle_changed(pane, entry, baseline_pid)

    # See comment in wait_for_text: push the blocking capture off the
    # main event loop via asyncio.to_thread.
    initial_content = await asyncio.to_thread(pane.capture_pane)
    start_time = time.monotonic()
    deadline = start_time + timeout
    changed = False

    try:
        while True:
            elapsed = time.monotonic() - start_time
            await _maybe_report_progress(
                ctx,
                progress=elapsed,
                total=timeout,
                message=f"Watching pane {pane.pane_id} for change",
            )

            state = await asyncio.to_thread(_read_pane_state, pane)
            _raise_if_pane_lifecycle_changed(pane, state, baseline_pid)
            current = await asyncio.to_thread(pane.capture_pane)
            if current != initial_content:
                changed = True
                break

            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # MCP cancellation — see ``wait_for_text`` for rationale.
        logger.debug(
            "wait_for_content_change cancelled after %.3fs on pane %s",
            time.monotonic() - start_time,
            pane.pane_id,
        )
        raise

    elapsed = time.monotonic() - start_time
    if not changed:
        await _maybe_log(
            ctx,
            level="warning",
            message=(
                f"No content change in pane {pane.pane_id} before {timeout}s timeout"
            ),
        )
    return ContentChangeResult(
        changed=changed,
        pane_id=pane.pane_id,
        elapsed_seconds=round(elapsed, 3),
    )
