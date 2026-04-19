"""Tests for libtmux MCP server tools."""

from __future__ import annotations

import os
import pathlib
import typing as t

import pytest

from libtmux_mcp.tools.server_tools import (
    create_session,
    get_server_info,
    kill_server,
    list_servers,
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
                environment=environment,
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


@pytest.mark.usefixtures("mcp_session")
def test_list_servers_finds_live_socket(mcp_server: Server) -> None:
    """``list_servers`` enumerates the current user's tmux sockets.

    The fixture server is a real tmux process with a real socket
    under ``$TMUX_TMPDIR/tmux-$UID/``; the discovery tool must see
    it and report it alive.
    """
    from libtmux_mcp.models import ServerInfo

    results = list_servers()
    assert isinstance(results, list)
    assert all(isinstance(r, ServerInfo) for r in results)
    names = [r.socket_name for r in results]
    assert mcp_server.socket_name in names
    # The fixture's socket must be reported alive.
    found = next(r for r in results if r.socket_name == mcp_server.socket_name)
    assert found.is_alive is True


def test_list_servers_missing_tmpdir_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``tmux-<uid>/`` directory → empty list, no error.

    On a freshly provisioned container or a user who has never run
    tmux, the directory does not exist. The tool must degrade
    gracefully rather than raising.
    """
    monkeypatch.setenv("TMUX_TMPDIR", "/nonexistent-list-servers-test")
    results = list_servers()
    assert results == []


@pytest.mark.usefixtures("mcp_session")
def test_list_servers_extra_socket_paths_surfaces_custom_path(
    mcp_server: Server,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``extra_socket_paths`` surfaces a ``tmux -S /path/...`` daemon.

    Regression guard: the canonical ``$TMUX_TMPDIR`` scan misses any
    tmux started with ``-S /arbitrary/path``. ``extra_socket_paths``
    lets callers who know about such paths include them in the result
    without having to do a second tool call.

    Re-uses the pytest-libtmux fixture socket as the "extra" path by
    pointing the canonical scan at an empty dir — that proves the
    extra-paths code path is the reason the server appears in the
    result, not the canonical scan.
    """
    from libtmux_mcp.models import ServerInfo

    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    fixture_socket = (
        pathlib.Path("/tmp")
        / f"tmux-{os.geteuid()}"
        / (mcp_server.socket_name or "default")
    )
    assert fixture_socket.is_socket(), "fixture socket must exist for the test"

    results = list_servers(extra_socket_paths=[str(fixture_socket)])

    assert isinstance(results, list)
    # Canonical scan saw an empty tmpdir, so everything below came from
    # the extra-paths probe.
    socket_paths = [r.socket_path for r in results]
    assert str(fixture_socket) in socket_paths
    found = next(r for r in results if r.socket_path == str(fixture_socket))
    assert isinstance(found, ServerInfo)
    assert found.is_alive is True


def test_list_servers_extra_socket_paths_skips_nonexistent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Nonexistent / non-socket extras are silently skipped, not fatal.

    Agents supplying stale paths (stored from a previous session,
    config file, etc.) must not crash the whole discovery call.
    """
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    bogus = tmp_path / "never-existed.sock"
    regular_file = tmp_path / "not-a-socket.txt"
    regular_file.write_text("decoy")

    results = list_servers(
        extra_socket_paths=[str(bogus), str(regular_file)],
    )
    assert results == []
