"""Tests for tmux ``wait-for`` channel tools."""

from __future__ import annotations

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


def test_signal_channel_no_waiter_is_noop(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``tmux wait-for -S`` on an unwaited channel returns successfully.

    Depends on ``mcp_session`` rather than bare ``mcp_server`` so the
    tmux server process is actually running — the Server fixture only
    constructs an unstarted Server instance.
    """
    del mcp_session  # forces server boot via fixture dependency
    result = signal_channel(
        channel="wf_test_noop",
        socket_name=mcp_server.socket_name,
    )
    assert "signalled" in result


def test_wait_for_channel_returns_when_signalled(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A signalled channel unblocks ``wait_for_channel`` immediately."""
    del mcp_session
    channel = "wf_signalled_test"

    def _signal_after_delay() -> None:
        time.sleep(0.3)
        signal_channel(channel=channel, socket_name=mcp_server.socket_name)

    thread = threading.Thread(target=_signal_after_delay)
    thread.start()
    try:
        result = wait_for_channel(
            channel=channel,
            timeout=5.0,
            socket_name=mcp_server.socket_name,
        )
        assert "signalled" in result
    finally:
        thread.join()


def test_wait_for_channel_times_out(mcp_server: Server, mcp_session: Session) -> None:
    """Unsignalled channel raises a timeout ``ToolError`` within the cap."""
    del mcp_session
    start = time.monotonic()
    with pytest.raises(ToolError, match="wait-for timeout"):
        wait_for_channel(
            channel="wf_timeout_test",
            timeout=0.5,
            socket_name=mcp_server.socket_name,
        )
    elapsed = time.monotonic() - start
    # Allow generous slack for tmux subprocess spawn overhead.
    assert elapsed < 3.0, f"timeout took unexpectedly long: {elapsed}s"


def test_wait_for_channel_rejects_invalid_name(mcp_server: Server) -> None:
    """Invalid channel names are rejected before spawning tmux."""
    with pytest.raises(ToolError, match="Invalid channel name"):
        wait_for_channel(
            channel="has space",
            timeout=1.0,
            socket_name=mcp_server.socket_name,
        )


def test_signal_channel_rejects_invalid_name(mcp_server: Server) -> None:
    """Invalid channel names are rejected before spawning tmux."""
    with pytest.raises(ToolError, match="Invalid channel name"):
        signal_channel(
            channel="has/slash",
            socket_name=mcp_server.socket_name,
        )
