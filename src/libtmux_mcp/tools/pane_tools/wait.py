"""Waiting / polling tools for pane content changes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import typing as t

import anyio
from fastmcp import Context
from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _get_server,
    _resolve_pane,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    ContentChangeResult,
    WaitForTextResult,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane

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


class _PaneState(t.NamedTuple):
    """Per-tick snapshot of pane state used by :func:`wait_for_text`.

    Read in one ``display-message`` round-trip so the loop costs two
    subprocesses per tick (state + capture) instead of growing
    linearly with each new field. ``|`` is the field separator —
    history/cursor/height are integers, ``pane_pid`` is a numeric PID
    string, and ``pane_dead`` is the literal ``"0"``/``"1"`` flag.
    """

    history_size: int
    cursor_y: int
    pane_height: int
    pane_pid: str
    pane_dead: bool


def _read_pane_state(pane: Pane) -> _PaneState:
    """Return a :class:`_PaneState` snapshot for ``pane``.

    Combines the per-tick reads ``wait_for_text`` needs into a single
    ``display-message`` call. ``history_size + cursor_y`` gives the
    absolute grid anchor at entry; ``pane_height`` gates the bottom-
    row capture clip; ``pane_pid`` and ``pane_dead`` surface
    respawn-pane and pane-death events that invalidate the baseline.
    """
    stdout = pane.display_message(
        "#{history_size}|#{cursor_y}|#{pane_height}|#{pane_pid}|#{pane_dead}",
        get_text=True,
    )
    raw = stdout[0] if stdout else "0|0|0||0"
    hs, cy, sy, pid, dead = raw.split("|", 4)
    return _PaneState(
        history_size=int(hs),
        cursor_y=int(cy),
        pane_height=int(sy),
        pane_pid=pid,
        pane_dead=dead == "1",
    )


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

    **What "new" means.** At entry the tool snapshots the pane's absolute
    grid position (``history_size + cursor_y``) and only matches lines
    written below that baseline. Stale scrollback that was already
    present when the call began is ignored. For the synchronous "is
    the pattern in the pane right now?" check, call
    {tooliconl}`search-panes` instead.

    **Adversarial-safety pattern.** If you cannot trust that the
    pattern only appears after your action — for example because the
    pane prints recurring prompts, log lines, or output from background
    processes you do not control — bracket your command with a unique
    sentinel: ``cmd; echo __WAIT_$RANDOM__`` and wait for the sentinel
    instead of ``cmd``'s natural output. tmux's grid model cannot
    distinguish "your output" from "theirs"; the sentinel can.

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
    **Scrollback truncation.** If ``history-limit`` is small and the
    baseline line rolls out of history during the wait, tmux clips
    ``-S`` to the oldest available line (``cmd-capture-pane.c``); the
    worst case degrades to pre-baseline behaviour on the surviving
    portion of history rather than an infinite false-match loop.

    **Reverse-index sequences (``\\eM``).** Programs that rewrite
    history below the baseline can theoretically re-introduce stale
    text into the captured range. This is rare on the main screen
    because pagers (``less``, ``more``) and other heavy users run on
    the alternate screen, which has a fresh grid and does not
    interact with the baseline.

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
        raise ToolError(msg)
    if interval < 0.01:
        msg = f"interval must be at least 0.01 s (received {interval})"
        raise ToolError(msg)
    if timeout <= 0:
        msg = f"timeout must be positive (received {timeout})"
        raise ToolError(msg)

    search_pattern = pattern if regex else re.escape(pattern)
    flags = 0 if match_case else re.IGNORECASE
    try:
        compiled = re.compile(search_pattern, flags)
    except re.error as e:
        msg = f"Invalid regex pattern: {e}"
        await _maybe_log(ctx, level="warning", message=msg)
        raise ToolError(msg) from e

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    assert pane.pane_id is not None

    # Anchor ``start_time`` before the baseline read so a stalled
    # tmux server cannot blow the user-supplied ``timeout`` budget
    # — libtmux's ``tmux_cmd`` uses ``Popen.communicate()`` with no
    # subprocess timeout, so the read can block arbitrarily long.
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
    pane_height = entry.pane_height
    baseline_pid = entry.pane_pid

    matched_lines: list[str] = []
    found = False

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
            if state.pane_dead:
                msg = f"pane {pane.pane_id} died during wait"
                raise ToolError(msg)
            if state.pane_pid != baseline_pid:
                msg = (
                    f"pane {pane.pane_id} was respawned during wait "
                    f"(pid {baseline_pid} -> {state.pane_pid}); "
                    "baseline anchor no longer valid"
                )
                raise ToolError(msg)
            # ``+ 1`` skips the baseline line itself so we don't
            # re-match the row the cursor sat on at entry.
            start_line = baseline_abs - state.history_size + 1
            # ``capture-pane -S`` clips a below-visible start back to
            # the bottom row (cmd-capture-pane.c, post-tmux-3.0), so a
            # naive capture would return stale bottom-row text whenever
            # no new rows have appeared below the cursor yet. Skip the
            # capture entirely on those ticks.
            if start_line >= pane_height:
                lines: list[str] = []
            else:
                lines = await asyncio.to_thread(
                    pane.capture_pane, start=start_line, end=None
                )
            hits = [line for line in lines if compiled.search(line)]
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
        timed_out=not found,
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
        timed_out=not changed,
    )
