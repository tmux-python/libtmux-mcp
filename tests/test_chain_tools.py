"""Tests for the chain command tools (one-dispatch tmux command sequences)."""

from __future__ import annotations

import asyncio
import typing as t

import pytest

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.models import ChainCommand, ForwardSplit
from libtmux_mcp.tools.chain_tools import build_forward_layout, run_command_chain

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session


def test_run_command_chain_one_dispatch(mcp_session: Session) -> None:
    """Two set-option commands take effect from a single tmux invocation."""
    server = mcp_session.server
    result = asyncio.run(
        run_command_chain(
            commands=[
                ChainCommand(command="set-option", args=["-g", "@cc_a", "1"]),
                ChainCommand(command="set-option", args=["-g", "@cc_b", "2"]),
            ],
            socket_name=server.socket_name,
        ),
    )

    assert result.returncode == 0
    assert result.command_count == 2
    assert ";" in result.argv  # the standalone separator proves one sequence
    assert server.cmd("show-option", "-gv", "@cc_a").stdout == ["1"]
    assert server.cmd("show-option", "-gv", "@cc_b").stdout == ["2"]


def test_run_command_chain_aborts_on_error(mcp_session: Session) -> None:
    """A failing command aborts the rest of the sequence (tmux ; semantics)."""
    server = mcp_session.server
    result = asyncio.run(
        run_command_chain(
            commands=[
                ChainCommand(command="rename-window", args=["x"], target="@999999"),
                ChainCommand(command="set-option", args=["-g", "@cc_sentinel", "set"]),
            ],
            socket_name=server.socket_name,
        ),
    )

    assert result.returncode != 0
    # the sequence aborted at the failing command, so the sentinel never ran:
    assert "set" not in server.cmd("show-option", "-gv", "@cc_sentinel").stdout


def test_run_command_chain_validation(mcp_session: Session) -> None:
    """An empty list and an empty-string target both fail closed."""
    socket = mcp_session.server.socket_name
    with pytest.raises(ExpectedToolError):
        asyncio.run(run_command_chain(commands=[], socket_name=socket))

    with pytest.raises(ExpectedToolError):
        asyncio.run(
            run_command_chain(
                commands=[ChainCommand(command="kill-window", target="")],
                socket_name=socket,
            ),
        )


def test_run_command_chain_blocks_kill_server(mcp_session: Session) -> None:
    """kill-server is refused outright and the server survives."""
    server = mcp_session.server
    with pytest.raises(ExpectedToolError):
        asyncio.run(
            run_command_chain(
                commands=[ChainCommand(command="kill-server")],
                socket_name=server.socket_name,
            ),
        )
    assert server.is_alive()


def test_build_forward_layout_captures_ids(mcp_server: Server, mcp_pane: Pane) -> None:
    """Two splits off a seed pane return two distinct, real pane ids."""
    result = asyncio.run(
        build_forward_layout(
            splits=[ForwardSplit(horizontal=True), ForwardSplit()],
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        ),
    )

    assert len(result.pane_ids) == 2
    assert result.pane_ids[0] != result.pane_ids[1]
    assert all(pid.startswith("%") for pid in result.pane_ids)
    assert result.dispatch_count >= 2  # independent splits need a dispatch each

    mcp_pane.window.refresh()
    existing = {p.pane_id for p in mcp_pane.window.panes}
    assert set(result.pane_ids) <= existing


def test_build_forward_layout_single_split_send_keys(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A lone split folds to one dispatch and its send_keys reaches the new pane."""
    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = "cc_fwd_layout"
    keys = f"printf 'CC_FWD\\n'; tmux wait-for -S {channel}"
    result = asyncio.run(
        build_forward_layout(
            splits=[ForwardSplit(send_keys=keys)],
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        ),
    )

    assert len(result.pane_ids) == 1
    assert result.dispatch_count == 1  # single split -> one {marked} invocation

    asyncio.run(
        wait_for_channel(channel, timeout=5.0, socket_name=mcp_server.socket_name),
    )
    mcp_pane.window.refresh()
    new_pane = mcp_pane.window.panes.get(pane_id=result.pane_ids[0])
    assert new_pane is not None
    assert "CC_FWD" in "\n".join(new_pane.capture_pane())


def test_build_forward_layout_validation(mcp_pane: Pane) -> None:
    """An empty split list fails closed."""
    with pytest.raises(ExpectedToolError):
        asyncio.run(build_forward_layout(splits=[], pane_id=mcp_pane.pane_id))
