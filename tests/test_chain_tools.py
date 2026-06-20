"""Tests for the chain command tools (one-dispatch tmux command sequences)."""

from __future__ import annotations

import asyncio
import typing as t

import pytest

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.models import ChainCommand
from libtmux_mcp.tools.chain_tools import run_command_chain

if t.TYPE_CHECKING:
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
