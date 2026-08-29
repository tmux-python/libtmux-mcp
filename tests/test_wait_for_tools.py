"""Tests for tmux ``wait-for`` channel tools."""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import re
import signal
import subprocess
import threading
import time
import typing as t
import uuid

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp import _progress as _progress_module
from libtmux_mcp.tools.wait_for_tools import (
    _validate_channel_name,
    signal_channel,
    wait_for_channel,
)

if t.TYPE_CHECKING:
    from libtmux.server import Server
    from libtmux.session import Session


@pytest.mark.parametrize(
    "name",
    ["tests_done", "deploy.prod", "ns:ready-2", "a", "x" * 128],
)
def test_validate_channel_name_accepts_valid(name: str) -> None:
    """Well-formed channel names pass through unchanged."""
    assert _validate_channel_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["", "has space", "with/slash", "x" * 129, "!bang", "semi;colon"],
)
def test_validate_channel_name_rejects_invalid(name: str) -> None:
    """Malformed channel names raise ToolError with the name quoted."""
    with pytest.raises(ToolError, match="Invalid channel name"):
        _validate_channel_name(name)


def test_channel_tools_are_coroutines() -> None:
    """Both tools must be ``async def`` so FastMCP awaits them.

    Regression guard for tmux-python/libtmux-mcp#18: sync ``def`` tools
    were direct-called on FastMCP's event loop and the internal
    ``subprocess.run`` blocked stdio for the full timeout window. The
    fix converts both to ``async def`` + ``asyncio.to_thread``; this
    assertion pins the async surface so a silent revert doesn't sneak
    through.
    """
    assert asyncio.iscoroutinefunction(wait_for_channel)
    assert asyncio.iscoroutinefunction(signal_channel)


@pytest.mark.usefixtures("mcp_session")
def test_signal_channel_no_waiter_is_noop(mcp_server: Server) -> None:
    """``tmux wait-for -S`` on an unwaited channel returns successfully.

    The ``mcp_session`` fixture is required even though the test does
    not touch it — the bare ``mcp_server`` fixture only constructs an
    unstarted Server instance, so ``mcp_session`` is what actually
    boots the tmux process.
    """
    result = asyncio.run(
        signal_channel(
            channel="wf_test_noop",
            socket_name=mcp_server.socket_name,
        )
    )
    assert "signalled" in result


@pytest.mark.usefixtures("mcp_session")
def test_wait_for_channel_returns_when_signalled(mcp_server: Server) -> None:
    """A signalled channel unblocks ``wait_for_channel`` immediately."""
    channel = "wf_signalled_test"

    def _signal_after_delay() -> None:
        time.sleep(0.3)
        asyncio.run(signal_channel(channel=channel, socket_name=mcp_server.socket_name))

    thread = threading.Thread(target=_signal_after_delay)
    thread.start()
    try:
        result = asyncio.run(
            wait_for_channel(
                channel=channel,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        assert "signalled" in result
    finally:
        thread.join()


@contextlib.contextmanager
def _throwaway_server(socket_name: str) -> t.Iterator[Server]:
    """Yield a private tmux server, then destroy it and its socket file.

    These tests kill the server they are waiting on, so the shared
    ``mcp_server`` fixture cannot be used.

    Cleanup has to unlink the socket by hand. A server killed with
    SIGKILL never gets to remove its own socket, so the file outlives
    it, and ``Server.socket_path`` is ``None`` on a server built from a
    ``socket_name`` — so the path is read from tmux itself while the
    server is still alive to answer.
    """
    from libtmux.server import Server as LibtmuxServer

    server = LibtmuxServer(socket_name=socket_name)
    server.new_session(session_name="s", detach=True)
    socket_path = server.cmd("display-message", "-p", "#{socket_path}").stdout[0]
    try:
        yield server
    finally:
        with contextlib.suppress(Exception):
            server.cmd("kill-server")
        with contextlib.suppress(Exception):
            pathlib.Path(socket_path).unlink(missing_ok=True)


class ServerDeathFixture(t.NamedTuple):
    """Test fixture for wait_for_channel's server-disappeared detection."""

    test_id: str
    #: How the doomed tmux server is taken down mid-wait.
    kill_mode: str
    #: Substring the raised error must contain. The clean-shutdown paths
    #: are caught by the liveness re-probe; SIGKILL is the one tmux
    #: already reports itself.
    expected_message: str


SERVER_DEATH_FIXTURES: list[ServerDeathFixture] = [
    ServerDeathFixture(
        test_id="clean_kill_server",
        kill_mode="kill-server",
        expected_message="no longer running",
    ),
    ServerDeathFixture(
        test_id="server_sigterm",
        kill_mode="sigterm",
        expected_message="no longer running",
    ),
    ServerDeathFixture(
        test_id="server_sigkill",
        kill_mode="sigkill",
        expected_message="server exited unexpectedly",
    ),
]


@pytest.mark.parametrize(
    ServerDeathFixture._fields,
    SERVER_DEATH_FIXTURES,
    ids=[f.test_id for f in SERVER_DEATH_FIXTURES],
)
def test_wait_for_channel_detects_a_vanished_server(
    test_id: str, kill_mode: str, expected_message: str
) -> None:
    """A server that dies mid-wait must not be reported as a signal.

    ``tmux wait-for`` exits 0 when the server shuts down cleanly without
    ever signalling the channel, which is byte-identical to the exit of
    a genuine signal — same code, same empty stderr. Only an unclean
    death (SIGKILL) reports itself. Without the liveness re-probe the
    two clean paths returned "Channel ... was signalled", which is the
    worst answer this tool can give: it exists to be the deterministic
    primitive the fuzzy pane-scraping waits defer to.

    Runs against its own throwaway server, since the test destroys the
    server it waits on.
    """
    socket_name = f"wfc_death_{test_id}_{os.getpid()}"
    with _throwaway_server(socket_name) as doomed:
        pid = int(doomed.cmd("display-message", "-p", "#{pid}").stdout[0])

        def _kill_after_delay() -> None:
            time.sleep(0.5)
            if kill_mode == "kill-server":
                doomed.cmd("kill-server")
            elif kill_mode == "sigterm":
                os.kill(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGKILL)

        thread = threading.Thread(target=_kill_after_delay)
        thread.start()
        try:
            with pytest.raises(ToolError) as excinfo:
                asyncio.run(
                    wait_for_channel(
                        channel="never_signalled",
                        timeout=10.0,
                        socket_name=socket_name,
                    )
                )
        finally:
            thread.join()

    message = str(excinfo.value)
    # Two honest paths describe a vanished server, and which one arrives
    # is a race this test cannot control: if the kill lands before the
    # wait-for child connects, tmux reports the absence ITSELF and the
    # liveness re-probe -- the thing under test -- never runs. Both
    # raise, so the tool never claims a signal either way; but only one
    # exercises the mechanism, so the other is an unestablished
    # precondition rather than a pass. Reproduced deterministically:
    # killing before the call gives "no server running on <path>",
    # killing during it gives "no longer running".
    if "no server running" in message:
        pytest.skip(
            "the server died before the wait-for child connected, so tmux "
            "reported the absence itself and the liveness re-probe never ran"
        )
    assert re.search(expected_message, message), message


def test_wait_for_channel_still_succeeds_on_a_live_server() -> None:
    """The liveness re-probe must not turn real signals into errors.

    Control for :func:`test_wait_for_channel_detects_a_vanished_server`:
    a re-probe aggressive enough to fail here would be worse than the
    bug it fixes. Uses a throwaway server so the two tests differ only
    in whether the server survives.
    """
    socket_name = f"wfc_alive_{os.getpid()}"
    with _throwaway_server(socket_name) as live:

        def _signal_after_delay() -> None:
            time.sleep(0.3)
            live.cmd("wait-for", "-S", "really_signalled")

        thread = threading.Thread(target=_signal_after_delay)
        thread.start()
        try:
            result = asyncio.run(
                wait_for_channel(
                    channel="really_signalled",
                    timeout=10.0,
                    socket_name=socket_name,
                )
            )
            assert "signalled" in result
        finally:
            thread.join()


@pytest.mark.usefixtures("mcp_session")
def test_wait_for_channel_times_out(mcp_server: Server) -> None:
    """Unsignalled channel raises a timeout ``ToolError`` within the cap."""
    start = time.monotonic()
    with pytest.raises(ToolError, match="wait-for timeout"):
        asyncio.run(
            wait_for_channel(
                channel="wf_timeout_test",
                timeout=0.5,
                socket_name=mcp_server.socket_name,
            )
        )
    elapsed = time.monotonic() - start
    # Allow generous slack for tmux subprocess spawn overhead.
    assert elapsed < 3.0, f"timeout took unexpectedly long: {elapsed}s"


@pytest.mark.usefixtures("mcp_session")
def test_wait_for_channel_clamps_oversized_timeout(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An over-large ``timeout`` is clamped to the server wait ceiling.

    Mirrors ``wait_for_text``'s clamp: without it, an unsignalled channel
    with ``timeout=3600`` would block the shared MCP connection for an
    hour instead of returning at the ceiling. The ceiling is lowered to
    1 s so the assertion is about the clamp mechanism, not wall-clock
    patience.
    """
    from libtmux_mcp import _wait_policy

    monkeypatch.setattr(_wait_policy, "_wait_max_seconds", 1.0)

    started = time.monotonic()
    with pytest.raises(ToolError, match=r"wait-for timeout.*within 1\.0s"):
        asyncio.run(
            wait_for_channel(
                channel="wf_clamp_test",
                timeout=3600.0,
                socket_name=mcp_server.socket_name,
            )
        )
    elapsed = time.monotonic() - started
    assert elapsed < 10.0, f"clamped wait ran {elapsed:.1f}s"


def test_wait_for_channel_rejects_invalid_name(mcp_server: Server) -> None:
    """Invalid channel names are rejected before spawning tmux."""
    with pytest.raises(ToolError, match="Invalid channel name"):
        asyncio.run(
            wait_for_channel(
                channel="has space",
                timeout=1.0,
                socket_name=mcp_server.socket_name,
            )
        )


def test_signal_channel_rejects_invalid_name(mcp_server: Server) -> None:
    """Invalid channel names are rejected before spawning tmux."""
    with pytest.raises(ToolError, match="Invalid channel name"):
        asyncio.run(
            signal_channel(
                channel="has/slash",
                socket_name=mcp_server.socket_name,
            )
        )


@pytest.mark.usefixtures("mcp_session")
def test_wait_for_channel_does_not_block_event_loop(mcp_server: Server) -> None:
    """Concurrent coroutines must make progress while the wait is pending.

    Regression guard for tmux-python/libtmux-mcp#18. Before the fix,
    ``subprocess.run`` blocked the FastMCP event loop for the full
    timeout; the ticker below would advance only between poll iterations
    (which there aren't any of — the subprocess is a single blocking
    call). With ``asyncio.to_thread`` the ticker must fire many times
    while the tmux subprocess waits for its signal.

    Discriminator: the wait is set to 0.5 s on an unsignalled channel.
    The ticker samples at 10 ms. With the fix we expect ≥ 20 ticks
    (500 ms / 10 ms = 50 nominal, halved to guard against CI jitter);
    without the fix we expect 0 — the event loop is pinned in
    ``subprocess.run`` until it times out.
    """

    async def _drive() -> int:
        ticks = 0
        stop = asyncio.Event()

        async def _ticker() -> None:
            nonlocal ticks
            while not stop.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        async def _waiter() -> None:
            try:
                with pytest.raises(ToolError, match="wait-for timeout"):
                    await wait_for_channel(
                        channel="wf_evtloop_test",
                        timeout=0.5,
                        socket_name=mcp_server.socket_name,
                    )
            finally:
                stop.set()

        await asyncio.gather(_ticker(), _waiter())
        return ticks

    # A blocked loop yields EXACTLY one tick however long the block
    # lasts and however many times you look; a STARVED loop yields one
    # too, which is the only reason to retry. Counting to 20 instead
    # assumed the loop ticks at ~100 Hz, and parallel load breaks that
    # -- the same assumption already measured failing on the
    # capture_since sibling.
    attempts = []
    for _ in range(3):
        attempts.append(asyncio.run(_drive()))
        if attempts[-1] >= 2:
            break
    assert max(attempts) >= 2, (
        f"ticker never advanced past one tick in {len(attempts)} attempts "
        f"({attempts}) — wait_for_channel is blocking the event loop "
        "instead of running the subprocess in a thread"
    )


@pytest.mark.usefixtures("mcp_session")
def test_wait_for_channel_propagates_cancellation(mcp_server: Server) -> None:
    """``wait_for_channel`` raises ``CancelledError`` (not ``ToolError``).

    MCP cancellation semantics: when a client cancels an in-flight tool
    call, the awaiting ``asyncio.Task`` receives ``CancelledError``.
    ``handle_tool_errors_async`` catches ``Exception`` (not
    ``BaseException``), and neither the narrow ``TimeoutError``
    except-block nor the kill-and-reap guard inside
    ``_run_tmux_bounded`` swallows ``CancelledError`` — so the
    cancellation propagates through the decorator naturally. This test
    locks that contract in so a future broadening of the catch
    (e.g. ``except BaseException``) trips immediately.

    Uses ``task.cancel()`` rather than ``asyncio.wait_for`` so the
    raised exception is the inner ``CancelledError`` directly.
    """

    async def _runner() -> None:
        task = asyncio.create_task(
            wait_for_channel(
                channel="wf_cancel_test",
                timeout=10.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await asyncio.sleep(0.1)  # let the tmux child spawn
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_runner())


def _tmux_wait_pids(socket_name: str, channel: str) -> list[int]:
    """Return pids of live ``tmux -L <socket> wait-for <channel>`` processes.

    Asks the OS rather than the tool: the defect this backs is exactly
    a tool that reports a clean cancellation while its child runs on.
    Matches the whole argv vector and skips this process, so neither
    the test runner's own command line nor the ``ps`` call itself can
    produce a false positive. Reaped children are absent from ``ps``
    and zombies render as ``[tmux] <defunct>``, which does not match.
    """
    want = ["tmux", "-L", socket_name, "wait-for", channel]
    listing = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    me = os.getpid()
    pids: list[int] = []
    for line in listing.splitlines():
        pid_field, _, args = line.strip().partition(" ")
        if not pid_field.isdigit() or int(pid_field) == me:
            continue
        if args.split() == want:
            pids.append(int(pid_field))
    return pids


@pytest.mark.usefixtures("mcp_session")
def test_wait_for_channel_kills_tmux_child_on_cancel(mcp_server: Server) -> None:
    """A cancelled wait must not leave its ``tmux wait-for`` child running.

    ``asyncio.to_thread(subprocess.run, ...)`` is uninterruptible: the
    coroutine raises ``CancelledError`` at once while the worker thread
    stays blocked in ``waitpid``, so the tmux child kept running for
    the whole remainder of its budget with nobody waiting on it.
    Measured before the fix: a 15 s wait cancelled at 2 s left the
    child alive another 13 s; through a real agent TUI with the ceiling
    raised to 120 s and a 90 s timeout, ~61 s past the user's Esc.

    Note the harm is the live process itself, not a stolen signal —
    tmux keeps the server-side waiter registered even after the client
    dies (verified against ``tmux wait-for``), so ``wait-for -S`` is
    swallowed either way. Only the process is observable, so that is
    what this asserts.
    """
    channel = "wf_cancel_reap_test"
    socket_name = mcp_server.socket_name
    assert socket_name is not None

    # One constant governs the call's budget and the window the probe
    # waits in, so they cannot drift: the cancel has to land while the
    # call is in flight. Both are ceilings, so a generous value is free
    # except on the loaded box that needs it.
    call_budget = 20.0

    async def _drive() -> list[int]:
        task = asyncio.create_task(
            wait_for_channel(
                channel=channel,
                timeout=call_budget,
                socket_name=socket_name,
            )
        )

        # Off the loop: the probe walks every entry in /proc, which is a
        # blocking call inside the event loop it is measuring.
        async def _pids() -> list[int]:
            return await asyncio.to_thread(_tmux_wait_pids, socket_name, channel)

        # Polled, not slept: a fixed wait asserts the child has spawned
        # by a wall-clock moment, which under parallel load it has not.
        # The loop exits the moment one appears.
        deadline = time.monotonic() + call_budget * 0.75
        while time.monotonic() < deadline and not await _pids():
            await asyncio.sleep(0.05)
        assert await _pids(), (
            "no tmux wait-for child observed before the cancel — the probe "
            "is broken, so a later 'no survivors' result would be vacuous"
        )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Poll rather than sleep once: the kill is synchronous but the
        # reap is not instantaneous. The window is far short of what the
        # child still has on its own budget, so a survivor here is an
        # orphan and not a slow teardown.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not await _pids():
                break
            await asyncio.sleep(0.05)
        return await _pids()

    survivors = asyncio.run(_drive())
    assert not survivors, (
        f"cancelled wait_for_channel orphaned tmux child(ren) {survivors}; "
        "the child outlives the cancellation for the rest of its timeout"
    )


@pytest.mark.usefixtures("mcp_session")
def test_the_silent_waits_now_report_progress(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two of the three waits told the client nothing until they returned.

    ``wait_for_text`` polls, so it reports from inside its own loop.
    ``run_command`` and ``wait_for_channel`` each await ONE
    ``tmux wait-for`` child, so a client watching a thirty-second call
    saw the same thing whether the command was running or the server had
    stopped answering.
    """
    from fastmcp import Client

    from libtmux_mcp.server import build_mcp_server

    socket_name = mcp_server.socket_name
    assert socket_name is not None
    channel = f"pgt_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(_progress_module, "_TICK_SECONDS", 0.05)
    seen: list[str] = []
    first_tick = asyncio.Event()

    async def _on_progress(
        progress: float, total: float | None, message: str | None
    ) -> None:
        seen.append(message or f"{progress}/{total}")
        first_tick.set()

    async def _exercise() -> None:
        async with Client(build_mcp_server(), progress_handler=_on_progress) as client:
            waiter = asyncio.create_task(
                client.call_tool(
                    "wait_for_channel",
                    {"channel": channel, "timeout": 8.0, "socket_name": socket_name},
                    raise_on_error=False,
                )
            )
            # Wait for the first tick rather than for a fixed window.
            # Load can only DELAY ticks, never add them, so a sleep is a
            # race this test loses in one direction only -- it was
            # observed failing at loadavg 60+ with zero ticks in 0.6 s.
            # The ceiling is a bound, not a spend: it returns the moment
            # a tick lands.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(first_tick.wait(), timeout=5.0)
            mcp_server.cmd("wait-for", "-S", channel)
            await waiter

    asyncio.run(_exercise())
    assert seen, "the client received no progress during the wait"
    assert any("elapsed" in message for message in seen), seen


@pytest.mark.usefixtures("mcp_session")
def test_a_fast_wait_reports_no_progress(mcp_server: Server) -> None:
    """The ticker must not fire on a call that returns immediately.

    Without this the assertion above is satisfied by a ticker that
    reports unconditionally, which would put a notification on the wire
    for every sub-second call.
    """
    from fastmcp import Client

    from libtmux_mcp.server import build_mcp_server

    socket_name = mcp_server.socket_name
    assert socket_name is not None
    channel = f"pgf_{uuid.uuid4().hex[:8]}"
    seen: list[str] = []

    async def _on_progress(
        progress: float, total: float | None, message: str | None
    ) -> None:
        seen.append(message or "")

    async def _exercise() -> t.Any:
        async with Client(build_mcp_server(), progress_handler=_on_progress) as client:
            mcp_server.cmd("wait-for", "-S", channel)
            return await client.call_tool(
                "wait_for_channel",
                {"channel": channel, "timeout": 8.0, "socket_name": socket_name},
                raise_on_error=False,
            )

    result = asyncio.run(_exercise())
    # An errored call also reports nothing, so the outcome is asserted
    # first: otherwise this passes for the wrong reason.
    assert result.is_error is False, result.content
    assert seen == [], f"a sub-second wait put {len(seen)} notifications on the wire"


def test_a_channel_signalled_past_the_flat_bound_still_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """The two bounding strategies are not interchangeable.

    ``tmux wait-for`` blocks until signalled, so ONE tmux call
    legitimately occupies the whole timeout -- up to the 120s wait
    ceiling, not the 5s flat bound every other tmux call gets. Routing
    this path through the flat bound would fail a channel signalled at
    six seconds, and would report it as ``the tmux server is
    unresponsive`` while the server was answering normally.

    Pinned because that refactor would pass every other test in the
    suite: nothing else signals a channel later than the flat bound.
    """
    from libtmux_mcp import _utils

    # Exercise the distinction, not the shipped constants: with the flat
    # bound at 0.5s, a signal at 1.5s is past it by the same logic that
    # 6s is past 5s, and costs the suite 1.5s instead of 6.
    monkeypatch.setattr(_utils, "_SYNC_CALL_TIMEOUT_SECONDS", 0.5)
    channel = f"slow_{uuid.uuid4().hex[:8]}"
    socket_name = mcp_server.socket_name
    assert socket_name is not None

    def signal_later() -> None:
        time.sleep(1.5)
        subprocess.run(
            ["tmux", "-L", socket_name, "wait-for", "-S", channel], check=False
        )

    thread = threading.Thread(target=signal_later, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        result = asyncio.run(
            wait_for_channel(channel=channel, timeout=20.0, socket_name=socket_name)
        )
    finally:
        thread.join(timeout=5)
    elapsed = time.monotonic() - started

    assert "was signalled" in result
    assert elapsed > 0.5, "returned before the flat bound; the wait never blocked"


@pytest.mark.parametrize(
    ("signals", "latched"),
    [
        pytest.param(1, True, id="one-signal-latches"),
        pytest.param(2, False, id="two-signals-clear-the-latch"),
        pytest.param(3, True, id="three-signals-latch-again"),
    ],
)
@pytest.mark.usefixtures("mcp_session")
def test_signalling_twice_clears_the_latch(
    mcp_server: Server, signals: int, latched: bool
) -> None:
    """Tmux's signal TOGGLES the latch rather than saturating.

    Pinned because ``signal_channel``'s description asserts it, and a
    cited measurement goes stale silently. A second signal on a latched
    channel with no waiter falls past tmux's ``!wc->woken`` guard into
    the wake-the-waiters path, finds none, and removes the channel --
    taking the latch with it.

    The cleared case spends its timeout on purpose: a wait that must NOT
    return is only observable by letting it expire.
    """
    channel = f"latch_{signals}"
    for _ in range(signals):
        asyncio.run(signal_channel(channel=channel, socket_name=mcp_server.socket_name))

    def _wait() -> str:
        return asyncio.run(
            wait_for_channel(
                channel=channel, timeout=1.0, socket_name=mcp_server.socket_name
            )
        )

    if latched:
        assert "signalled" in _wait()
    else:
        with pytest.raises(ToolError, match="was not signalled within"):
            _wait()
