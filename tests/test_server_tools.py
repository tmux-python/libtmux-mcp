"""Tests for libtmux MCP server tools."""

from __future__ import annotations

import typing as t

import pytest

from libtmux_mcp.tools.server_tools import (
    create_session,
    get_server_info,
    kill_server,
    list_sessions,
)

if t.TYPE_CHECKING:
    from libtmux.server import Server
    from libtmux.session import Session


def test_list_sessions(mcp_server: Server, mcp_session: Session) -> None:
    """list_sessions returns a list of SessionInfo models."""
    result = list_sessions(socket_name=mcp_server.socket_name)
    assert isinstance(result, list)
    assert len(result) >= 1
    session_ids = [s.session_id for s in result]
    assert mcp_session.session_id in session_ids


def test_list_sessions_empty_server(mcp_server: Server) -> None:
    """list_sessions returns empty list when no sessions."""
    # Kill all sessions first
    for s in mcp_server.sessions:
        s.kill()
    result = list_sessions(socket_name=mcp_server.socket_name)
    assert result == []


def test_create_session(mcp_server: Server) -> None:
    """create_session creates a new tmux session."""
    result = create_session(
        session_name="mcp_test_new",
        socket_name=mcp_server.socket_name,
    )
    assert result.session_name == "mcp_test_new"
    assert result.session_id is not None


def test_create_session_returns_active_pane_id(mcp_server: Server) -> None:
    """create_session exposes the initial pane id of the new session.

    Regression guard for the multi-agent-test finding: three of four
    agents (codex, gemini, cursor-agent) had to issue a follow-up
    ``list_panes`` call after ``create_session`` to discover the pane
    id they needed for ``load_buffer`` / ``paste_buffer`` workflows.
    libtmux guarantees ``Session.active_pane`` is non-None immediately
    after ``Server.new_session`` — the pane id is available without
    any extra tmux round-trip, so ``SessionInfo`` should expose it.

    The contract: ``result.active_pane_id`` is a tmux pane id string
    (``"%N"``) that matches the first pane returned by ``list_panes``
    for the session.
    """
    from libtmux_mcp.tools.window_tools import list_panes

    result = create_session(
        session_name="mcp_test_active_pane",
        socket_name=mcp_server.socket_name,
    )

    assert result.active_pane_id is not None
    assert result.active_pane_id.startswith("%")

    panes = list_panes(
        session_name="mcp_test_active_pane",
        socket_name=mcp_server.socket_name,
    )
    assert any(p.pane_id == result.active_pane_id for p in panes)


class CreateSessionEnvStringFixture(t.NamedTuple):
    """Fixture for create_session ``environment`` JSON-string coercion."""

    test_id: str
    environment: str
    expect_error: bool
    error_match: str | None


CREATE_SESSION_ENV_STRING_FIXTURES: list[CreateSessionEnvStringFixture] = [
    CreateSessionEnvStringFixture(
        test_id="string_env_valid",
        environment='{"LIBTMUX_MCP_TEST":"hello"}',
        expect_error=False,
        error_match=None,
    ),
    CreateSessionEnvStringFixture(
        test_id="string_env_invalid_json",
        environment="{bad json",
        expect_error=True,
        error_match="Invalid environment JSON",
    ),
    CreateSessionEnvStringFixture(
        test_id="string_env_not_object",
        environment='"just a string"',
        expect_error=True,
        error_match="environment must be a JSON object",
    ),
    CreateSessionEnvStringFixture(
        test_id="string_env_array",
        environment='["not","a","dict"]',
        expect_error=True,
        error_match="environment must be a JSON object",
    ),
]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "bug: create_session rejects JSON-string environment "
        "(Cursor composer-1/1.5 format). The existing filters-level "
        "workaround in _filtered_serialize has not been extended to "
        "the environment parameter. Fix lands in the next commit."
    ),
)
@pytest.mark.parametrize(
    CreateSessionEnvStringFixture._fields,
    CREATE_SESSION_ENV_STRING_FIXTURES,
    ids=[f.test_id for f in CREATE_SESSION_ENV_STRING_FIXTURES],
)
def test_create_session_environment_accepts_json_string(
    mcp_server: Server,
    test_id: str,
    environment: str,
    expect_error: bool,
    error_match: str | None,
) -> None:
    """create_session accepts ``environment`` as a JSON string.

    Regression guard for the Cursor composer-1/1.5 dict-stringification
    bug. Mirrors ``tests/test_utils.py::test_apply_filters`` which
    exercises the same fallback for the ``filters`` parameter on list
    tools. The four fixtures match the filters test's four cases:
    valid JSON object, invalid JSON, JSON that is not an object
    (string scalar), JSON that is a list rather than an object.
    """
    from fastmcp.exceptions import ToolError

    session_name = f"mcp_env_str_{test_id}"
    if expect_error:
        assert error_match is not None
        with pytest.raises(ToolError, match=error_match):
            create_session(
                session_name=session_name,
                environment=t.cast("t.Any", environment),
                socket_name=mcp_server.socket_name,
            )
        return

    result = create_session(
        session_name=session_name,
        environment=t.cast("t.Any", environment),
        socket_name=mcp_server.socket_name,
    )
    assert result.session_name == session_name

    # Verify the environment variable was actually applied on the
    # tmux server — this is the end-to-end contract, not just
    # "doesn't raise".
    show_env = mcp_server.cmd(
        "show-environment", "-t", session_name, "LIBTMUX_MCP_TEST"
    )
    assert any("LIBTMUX_MCP_TEST=hello" in line for line in show_env.stdout)


def test_create_session_duplicate(mcp_server: Server, mcp_session: Session) -> None:
    """create_session raises error for duplicate session name."""
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        create_session(
            session_name=mcp_session.session_name,
            socket_name=mcp_server.socket_name,
        )


def test_get_server_info(mcp_server: Server, mcp_session: Session) -> None:
    """get_server_info returns server status."""
    result = get_server_info(socket_name=mcp_server.socket_name)
    assert result.is_alive is True
    assert result.session_count >= 1


class ListSessionsFilterFixture(t.NamedTuple):
    """Test fixture for list_sessions with filters."""

    test_id: str
    filters: dict[str, str] | None
    expected_count: int | None
    expect_error: bool
    error_match: str | None


LIST_SESSIONS_FILTER_FIXTURES: list[ListSessionsFilterFixture] = [
    ListSessionsFilterFixture(
        test_id="no_filters",
        filters=None,
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ListSessionsFilterFixture(
        test_id="exact_session_name",
        filters={"session_name": "<session_name>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ListSessionsFilterFixture(
        test_id="contains_operator",
        filters={"session_name__contains": "<partial>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ListSessionsFilterFixture(
        test_id="startswith_operator",
        filters={"session_name__startswith": "<partial>"},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ListSessionsFilterFixture(
        test_id="regex_operator",
        filters={"session_name__regex": ".*"},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ListSessionsFilterFixture(
        test_id="icontains_operator",
        filters={"session_name__icontains": "<partial_upper>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ListSessionsFilterFixture(
        test_id="no_match",
        filters={"session_name": "nonexistent_xyz_999"},
        expected_count=0,
        expect_error=False,
        error_match=None,
    ),
    ListSessionsFilterFixture(
        test_id="invalid_operator",
        filters={"session_name__badop": "test"},
        expected_count=None,
        expect_error=True,
        error_match="Invalid filter operator",
    ),
    ListSessionsFilterFixture(
        test_id="multiple_filters",
        filters={"session_name__contains": "<partial>", "session_name__regex": ".*"},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
]


@pytest.mark.parametrize(
    ListSessionsFilterFixture._fields,
    LIST_SESSIONS_FILTER_FIXTURES,
    ids=[f.test_id for f in LIST_SESSIONS_FILTER_FIXTURES],
)
def test_list_sessions_with_filters(
    mcp_server: Server,
    mcp_session: Session,
    test_id: str,
    filters: dict[str, str] | None,
    expected_count: int | None,
    expect_error: bool,
    error_match: str | None,
) -> None:
    """list_sessions supports QueryList filtering."""
    from fastmcp.exceptions import ToolError

    if filters is not None:
        session_name = mcp_session.session_name
        assert session_name is not None
        resolved: dict[str, str] = {}
        for k, v in filters.items():
            if v == "<session_name>":
                resolved[k] = session_name
            elif v == "<partial>":
                resolved[k] = session_name[:4]
            elif v == "<partial_upper>":
                resolved[k] = session_name[:4].upper()
            else:
                resolved[k] = v
        filters = resolved

    if expect_error:
        with pytest.raises(ToolError, match=error_match):
            list_sessions(
                socket_name=mcp_server.socket_name,
                filters=filters,
            )
    else:
        result = list_sessions(
            socket_name=mcp_server.socket_name,
            filters=filters,
        )
        assert isinstance(result, list)
        if expected_count is not None:
            assert len(result) == expected_count
        else:
            assert len(result) >= 1


def test_kill_server(
    mcp_server: Server, mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill_server kills the tmux server."""
    # Remove TMUX_PANE to bypass self-kill guard (test server is separate)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    result = kill_server(socket_name=mcp_server.socket_name)
    assert "killed" in result.lower()


def test_kill_server_self_kill_guard(
    mcp_server: Server, mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill_server refuses when the caller shares the target's socket."""
    from fastmcp.exceptions import ToolError

    from libtmux_mcp._utils import _effective_socket_path

    socket_path = _effective_socket_path(mcp_server)
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$0")
    monkeypatch.setenv("TMUX_PANE", "%99")
    with pytest.raises(ToolError, match="Refusing to kill"):
        kill_server(socket_name=mcp_server.socket_name)


def test_kill_server_allows_cross_socket(
    mcp_server: Server, mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """kill_server is allowed when the caller is on a different socket."""
    monkeypatch.setenv("TMUX", "/tmp/tmux-99999/unrelated-socket,12345,$0")
    monkeypatch.setenv("TMUX_PANE", "%99")
    result = kill_server(socket_name=mcp_server.socket_name)
    assert "killed" in result.lower()


def test_read_heavy_tools_return_pydantic_models(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``list_sessions`` and ``get_server_info`` return Pydantic models.

    Regression guard: bare-string returns on read-heavy tools drop
    machine-readable ``outputSchema`` from the MCP registration, which
    forces agents to re-parse strings. Keep these typed.
    """
    from libtmux_mcp.models import ServerInfo, SessionInfo

    sessions = list_sessions(socket_name=mcp_server.socket_name)
    assert isinstance(sessions, list)
    assert all(isinstance(s, SessionInfo) for s in sessions)

    info = get_server_info(socket_name=mcp_server.socket_name)
    assert isinstance(info, ServerInfo)
