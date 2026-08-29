"""Tests for libtmux MCP environment tools."""

from __future__ import annotations

import typing as t

from libtmux_mcp.models import EnvironmentResult
from libtmux_mcp.tools.env_tools import (
    set_environment,
    show_environment,
    unset_environment,
)

if t.TYPE_CHECKING:
    from libtmux.server import Server
    from libtmux.session import Session


def test_show_environment(mcp_server: Server, mcp_session: Session) -> None:
    """show_environment returns EnvironmentResult model."""
    result = show_environment(socket_name=mcp_server.socket_name)
    assert isinstance(result, EnvironmentResult)
    assert isinstance(result.variables, dict)


def test_set_environment(mcp_server: Server, mcp_session: Session) -> None:
    """set_environment sets an environment variable."""
    result = set_environment(
        name="MCP_TEST_VAR",
        value="test_value",
        socket_name=mcp_server.socket_name,
    )
    assert result.status == "set"
    assert result.name == "MCP_TEST_VAR"


def test_set_and_show_environment(mcp_server: Server, mcp_session: Session) -> None:
    """set_environment value is readable via show_environment."""
    set_environment(
        name="MCP_ROUND_TRIP",
        value="hello",
        socket_name=mcp_server.socket_name,
    )
    result = show_environment(socket_name=mcp_server.socket_name)
    assert result.variables.get("MCP_ROUND_TRIP") == "hello"


def test_show_environment_session(mcp_server: Server, mcp_session: Session) -> None:
    """show_environment can target a specific session."""
    result = show_environment(
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, EnvironmentResult)
    assert isinstance(result.variables, dict)


def test_show_environment_separates_removed_from_set(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A removed variable is a name, not a value of ``True``.

    tmux prints a variable marked removed as ``-NAME``. Keeping the
    dash in the key put the removal in the key -- so a lookup by the
    real name raised KeyError -- and giving it ``True`` read as "set to
    true" for a variable that is explicitly unset.
    """
    mcp_session.cmd("set-environment", "MCP_ENV_KEPT", "yes")
    mcp_session.cmd("set-environment", "-r", "MCP_ENV_GONE")

    result = show_environment(
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )

    assert result.variables["MCP_ENV_KEPT"] == "yes"
    assert "MCP_ENV_GONE" in result.removed
    assert "MCP_ENV_GONE" not in result.variables
    assert not any(name.startswith("-") for name in result.variables)


def test_set_environment_refuses_a_flag_shaped_name(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A name tmux would read as a flag must not reach tmux.

    ``set_environment(name="-u", value="VICTIM")`` ran
    ``set-environment -u VICTIM``, which DELETED VICTIM, and reported
    ``status="set"``. libtmux emits ``[name, value]`` with no ``--``.
    """
    import pytest
    from fastmcp.exceptions import ToolError

    set_environment(name="VICTIM", value="precious", socket_name=mcp_server.socket_name)

    for name in ("-u", "-r", "A;kill-server"):
        with pytest.raises(ToolError, match="Environment variable name must match"):
            set_environment(
                name=name, value="VICTIM", socket_name=mcp_server.socket_name
            )

    survivors = show_environment(socket_name=mcp_server.socket_name).variables
    assert survivors.get("VICTIM") == "precious"


def test_unset_environment_removes_a_variable(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Unset is reachable through the API, not only through the flag bug."""
    socket = mcp_server.socket_name
    set_environment(name="KEEPME", value="v1", socket_name=socket)
    set_environment(name="DROPME", value="v2", socket_name=socket)

    result = unset_environment(name="DROPME", socket_name=socket)
    assert result.status == "unset"
    assert result.value is None

    variables = show_environment(socket_name=socket).variables
    assert "DROPME" not in variables
    assert variables.get("KEEPME") == "v1"


def test_unset_environment_distinguishes_removal_from_no_op(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Tmux exits 0 either way, so read before removing.

    "I removed it" and "there was nothing there" call for different
    reactions when a caller is reconciling state, and afterwards they
    are indistinguishable.
    """
    socket = mcp_server.socket_name
    set_environment(name="REALVAR", value="x", socket_name=socket)

    assert unset_environment(name="REALVAR", socket_name=socket).status == "unset"
    assert unset_environment(name="NEVERSET", socket_name=socket).status == "absent"
    assert show_environment(socket_name=socket).scope_queried == "global"
    assert (
        show_environment(
            session_name=mcp_session.session_name, socket_name=socket
        ).scope_queried
        == "session"
    )


def test_unset_environment_distinguishes_absent_from_inherited(
    mcp_server: Server, mcp_session: Session
) -> None:
    """'absent' covered two situations with opposite consequences.

    Unsetting a globally-set name at session scope removes nothing and
    reported the same 'absent' as a name that never existed -- while
    every new pane kept receiving it.
    """
    mcp_server.cmd("set-environment", "-g", "QA_GLOB", "g1")

    shadowed = unset_environment(
        name="QA_GLOB",
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert shadowed.status == "absent"
    assert shadowed.still_set_globally is True

    never = unset_environment(
        name="QA_NEVER_EXISTED",
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert never.status == "absent"
    assert never.still_set_globally is False


def test_show_environment_can_answer_what_a_new_pane_gets(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A session view alone omits globals the next pane will inherit."""
    mcp_server.cmd("set-environment", "-g", "QA_GLOB", "g1")
    mcp_session.cmd("set-environment", "QA_SESS", "s1")

    at_scope = show_environment(
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert "QA_SESS" in at_scope.variables
    assert "QA_GLOB" not in at_scope.variables
    assert at_scope.include_inherited is False

    in_force = show_environment(
        session_name=mcp_session.session_name,
        include_inherited=True,
        socket_name=mcp_server.socket_name,
    )
    assert in_force.variables["QA_SESS"] == "s1"
    assert in_force.variables["QA_GLOB"] == "g1"
    assert in_force.include_inherited is True


def test_show_environment_session_value_wins_over_the_global(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Merge order must match the precedence a spawned pane sees."""
    mcp_server.cmd("set-environment", "-g", "QA_BOTH", "from-global")
    mcp_session.cmd("set-environment", "QA_BOTH", "from-session")

    in_force = show_environment(
        session_name=mcp_session.session_name,
        include_inherited=True,
        socket_name=mcp_server.socket_name,
    )
    assert in_force.variables["QA_BOTH"] == "from-session"
