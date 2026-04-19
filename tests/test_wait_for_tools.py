"""Tests for tmux ``wait-for`` channel tools."""

from __future__ import annotations

import asyncio
import threading
import time
import typing as t

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp.tools.wait_for_tools import (
    _validate_channel_name,
    signal_channel,
    wait_for_channel,
)

if t.TYPE_CHECKING:
    from libtmux.server import Server


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
                timeout=5.0,
                socket_name=mcp_server.socket_name,
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

    ticks = asyncio.run(_drive())
    assert ticks >= 20, (
        f"ticker advanced only {ticks} times — wait_for_channel is blocking "
        f"the event loop instead of running the subprocess in a thread"
    )


@pytest.mark.usefixtures("mcp_session")
def test_wait_for_channel_propagates_cancellation(mcp_server: Server) -> None:
    """``wait_for_channel`` raises ``CancelledError`` (not ``ToolError``).

    MCP cancellation semantics: when a client cancels an in-flight tool
    call, the awaiting ``asyncio.Task`` receives ``CancelledError``.
    ``handle_tool_errors_async`` catches ``Exception`` (not
    ``BaseException``), and the function's narrow ``subprocess.*``
    except-blocks cannot swallow ``CancelledError`` either — so the
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
        await asyncio.sleep(0.1)  # let the to_thread handoff start
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_runner())
