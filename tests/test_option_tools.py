"""Tests for libtmux MCP option tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp.tools.option_tools import set_option, show_option

if t.TYPE_CHECKING:
    from libtmux.server import Server
    from libtmux.session import Session


def test_show_option(mcp_server: Server, mcp_session: Session) -> None:
    """show_option returns an OptionResult model."""
    result = show_option(
        option="base-index",
        scope="session",
        global_=True,
        socket_name=mcp_server.socket_name,
    )
    assert result.option == "base-index"
    assert result.value is not None


def test_show_option_invalid_scope(mcp_server: Server, mcp_session: Session) -> None:
    """show_option raises ToolError on invalid scope."""
    with pytest.raises(ToolError, match="Invalid scope"):
        show_option(
            option="base-index",
            scope="global",  # type: ignore[arg-type]
            socket_name=mcp_server.socket_name,
        )


def test_show_option_target_without_scope(
    mcp_server: Server, mcp_session: Session
) -> None:
    """show_option raises ToolError when target is given without scope."""
    with pytest.raises(ToolError, match="scope is required"):
        show_option(
            option="base-index",
            target="some_session",
            socket_name=mcp_server.socket_name,
        )


def test_set_option(mcp_server: Server, mcp_session: Session) -> None:
    """set_option sets a tmux option."""
    result = set_option(
        option="display-time",
        value="3000",
        scope="server",
        global_=True,
        socket_name=mcp_server.socket_name,
    )
    assert result.status == "set"
    assert result.option == "display-time"


def test_show_option_resolves_inherited_values(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``include_inherited`` answers what is in force, not what is set here.

    A bare session-scope read of an inherited option returns ``None``,
    which reads as "unset" when the value really is in effect. tmux has
    ``-A`` for this; it was not exposed, so an agent could ask "is it
    set at this exact scope?" but never "what is in force?" -- and the
    latter is what a question like "is mouse mode on?" means.
    """
    plain = show_option(
        option="history-limit",
        scope="session",
        target=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    inherited = show_option(
        option="history-limit",
        scope="session",
        target=mcp_session.session_name,
        include_inherited=True,
        socket_name=mcp_server.socket_name,
    )

    assert plain.value is None
    assert plain.scope_queried == "session"
    assert plain.include_inherited is False
    # The value actually in force is reachable now.
    assert inherited.value is not None
    assert int(str(inherited.value)) > 0
    assert inherited.include_inherited is True


def test_set_option_refuses_a_flag_shaped_name(
    mcp_server: Server, mcp_session: Session
) -> None:
    """An option name tmux would read as a flag must not reach tmux.

    ``set_option(option="-g", value="x")`` had ``-g`` eaten as the
    global flag, leaving ``x`` as the option name -- which tmux
    prefix-matched to ``xterm-keys`` and turned off, while the result
    reported ``status="set"`` for an option the caller never named.
    """
    import pytest
    from fastmcp.exceptions import ToolError

    before = show_option(
        option="xterm-keys", scope="server", socket_name=mcp_server.socket_name
    ).value

    for name in ("-g", "-u"):
        with pytest.raises(ToolError, match="may not begin with"):
            set_option(option=name, value="x", socket_name=mcp_server.socket_name)

    after = show_option(
        option="xterm-keys", scope="server", socket_name=mcp_server.socket_name
    ).value
    assert after == before


def test_option_results_disclose_what_tmux_resolved(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Echoing the caller's spelling back proves nothing changed where.

    tmux accepts an unambiguous prefix, so ``history-lim`` sets
    ``history-limit``; and an untargeted query resolves through a
    "current" session chosen from attached clients, which an MCP client
    does not have.
    """
    socket = mcp_server.socket_name
    written = set_option(
        option="history-lim", value="8888", global_=True, socket_name=socket
    )
    assert written.option == "history-lim"
    assert written.resolved_option == "history-limit"

    read = show_option(option="history-limit", scope="session", socket_name=socket)
    assert read.resolved_target is not None
