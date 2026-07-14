"""Tests for libtmux MCP server tools."""

from __future__ import annotations

import concurrent.futures
import os
import pathlib
import threading
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
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session

    from libtmux_mcp.models import CallerContext


def _call_where_am_i() -> CallerContext:
    """Call the public invocation-discovery function under test."""
    from libtmux_mcp.tools import server_tools

    function = getattr(server_tools, "where_am_i", None)
    assert function is not None, "where_am_i is not implemented"
    return t.cast("CallerContext", function())


def _clear_configured_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the frozen caller environment as the effective target."""
    monkeypatch.delenv("LIBTMUX_SOCKET", raising=False)
    monkeypatch.delenv("LIBTMUX_SOCKET_PATH", raising=False)


def test_where_am_i_resolves_live_nondefault_caller(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live caller reports its current pane, window, and session."""
    from libtmux_mcp import models
    from libtmux_mcp._utils import _effective_socket_path
    from libtmux_mcp.server import _safety_level, _suppress_history

    caller_context_type = getattr(models, "CallerContext", None)
    assert caller_context_type is not None, "CallerContext is not implemented"

    _clear_configured_target(monkeypatch)
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    pane_id = mcp_pane.pane_id
    assert pane_id is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", pane_id)

    result = _call_where_am_i()

    assert isinstance(result, caller_context_type)
    assert result.inside_tmux is True
    assert result.self_available is True
    assert result.pane_id == mcp_pane.pane_id
    assert result.window_id == mcp_pane.window_id
    assert result.session_id == mcp_pane.session.session_id
    assert result.caller_socket_path == socket_path
    assert result.effective_socket_name is None
    assert result.effective_socket_path == socket_path
    assert result.server_running is True
    assert result.safety_level == _safety_level
    assert result.suppress_history is _suppress_history


def test_where_am_i_uses_effective_server_tmux_binary(
    mcp_server: Server,
    mcp_pane: Pane,
    custom_tmux_bin_without_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller lookup uses configured tmux even when PATH has no tmux."""
    from libtmux_mcp._utils import _effective_socket_path

    _clear_configured_target(monkeypatch)
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    assert mcp_pane.pane_id is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id)

    result = _call_where_am_i()

    assert custom_tmux_bin_without_path.endswith("configured-tmux")
    assert result.self_available is True
    assert result.pane_id == mcp_pane.pane_id


def test_where_am_i_reraises_unrelated_live_server_lookup_error(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live operational errors flow through the standard tmux mapper."""
    from libtmux import exc
    from libtmux.pane import Pane

    from libtmux_mcp._utils import ExpectedToolError, _effective_socket_path

    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    assert mcp_pane.pane_id is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id)

    def fail_lookup(cls: type[Pane], server: Server, pane_id: str) -> Pane:
        del cls, server, pane_id
        msg = "injected live caller lookup failure"
        raise exc.LibTmuxException(msg)

    monkeypatch.setattr(Pane, "from_pane_id", classmethod(fail_lookup))

    with pytest.raises(
        ExpectedToolError,
        match="tmux error: injected live caller lookup failure",
    ):
        _call_where_am_i()


def test_where_am_i_reports_dead_when_server_dies_during_lookup(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operational lookup error becomes unavailable only after death."""
    from libtmux import exc
    from libtmux.pane import Pane

    from libtmux_mcp._utils import _effective_socket_path

    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    assert mcp_pane.pane_id is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id)

    def kill_and_fail(cls: type[Pane], server: Server, pane_id: str) -> Pane:
        del cls, pane_id
        server.kill()
        msg = "injected dead caller lookup failure"
        raise exc.LibTmuxException(msg)

    monkeypatch.setattr(Pane, "from_pane_id", classmethod(kill_and_fail))

    result = _call_where_am_i()

    assert result.self_available is False
    assert result.server_running is False


def test_where_am_i_preserves_caller_on_configured_target_mismatch(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured-target mismatch keeps identity but denies self scope."""
    from libtmux.pane import Pane

    def fail_from_env(
        cls: type[Pane], environment: t.Mapping[str, str] | None = None
    ) -> Pane:
        del cls, environment
        pytest.fail("Pane.from_env must not run for a configured-target mismatch")

    monkeypatch.setattr(Pane, "from_env", classmethod(fail_from_env))
    socket_name = mcp_server.socket_name
    assert socket_name is not None
    pane_id = mcp_pane.pane_id
    assert pane_id is not None
    caller_socket = "/tmp/tmux-99999/caller-only"
    monkeypatch.setenv("LIBTMUX_SOCKET", socket_name)
    monkeypatch.delenv("LIBTMUX_SOCKET_PATH", raising=False)
    monkeypatch.setenv("TMUX", f"{caller_socket},12345,$0")
    monkeypatch.setenv("TMUX_PANE", pane_id)

    result = _call_where_am_i()

    assert result.inside_tmux is True
    assert result.self_available is False
    assert result.pane_id == mcp_pane.pane_id
    assert result.window_id is None
    assert result.session_id is None
    assert result.caller_socket_path == caller_socket
    assert result.effective_socket_name == socket_name
    assert result.effective_socket_path is None
    assert result.server_running is True


def test_where_am_i_returns_typed_state_for_dead_server(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead matching server is routine absence, not a tool error."""
    dead_socket = tmp_path / "dead.sock"
    monkeypatch.delenv("LIBTMUX_SOCKET", raising=False)
    monkeypatch.setenv("LIBTMUX_SOCKET_PATH", str(dead_socket))
    monkeypatch.setenv("TMUX", f"{dead_socket},12345,$0")
    monkeypatch.setenv("TMUX_PANE", "%404")

    result = _call_where_am_i()

    assert result.inside_tmux is True
    assert result.self_available is False
    assert result.pane_id == "%404"
    assert result.window_id is None
    assert result.session_id is None
    assert result.caller_socket_path == str(dead_socket)
    assert result.effective_socket_name is None
    assert result.effective_socket_path == str(dead_socket)
    assert result.server_running is False


@pytest.mark.usefixtures("mcp_session")
def test_where_am_i_returns_typed_state_for_stale_pane(
    mcp_server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale caller pane remains visible without invented parents."""
    from libtmux_mcp._utils import _effective_socket_path

    _clear_configured_target(monkeypatch)
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$0")
    monkeypatch.setenv("TMUX_PANE", "%999999")

    result = _call_where_am_i()

    assert result.inside_tmux is True
    assert result.self_available is False
    assert result.pane_id == "%999999"
    assert result.window_id is None
    assert result.session_id is None
    assert result.caller_socket_path == socket_path
    assert result.effective_socket_path == socket_path
    assert result.server_running is True


def test_where_am_i_returns_typed_state_outside_tmux(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outside-tmux invocation returns false and null identity fields."""
    dead_socket = tmp_path / "outside.sock"
    monkeypatch.delenv("LIBTMUX_SOCKET", raising=False)
    monkeypatch.setenv("LIBTMUX_SOCKET_PATH", str(dead_socket))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    result = _call_where_am_i()

    assert result.inside_tmux is False
    assert result.self_available is False
    assert result.pane_id is None
    assert result.window_id is None
    assert result.session_id is None
    assert result.caller_socket_path is None
    assert result.effective_socket_name is None
    assert result.effective_socket_path == str(dead_socket)
    assert result.server_running is False


def test_caller_context_is_frozen(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invocation-discovery results cannot drift after construction."""
    from pydantic import ValidationError

    monkeypatch.setenv("LIBTMUX_SOCKET_PATH", str(tmp_path / "frozen.sock"))
    result = _call_where_am_i()

    with pytest.raises(ValidationError):
        result.pane_id = "%changed"


def test_list_sessions(mcp_server: Server, mcp_session: Session) -> None:
    """list_sessions returns a typed page of SessionInfo models."""
    from libtmux_mcp.models import SessionPage

    result = list_sessions(socket_name=mcp_server.socket_name)
    assert isinstance(result, SessionPage)
    assert len(result.items) >= 1
    session_ids = [session.session_id for session in result.items]
    assert mcp_session.session_id in session_ids


def test_list_sessions_empty_server(mcp_server: Server) -> None:
    """list_sessions returns empty list when no sessions."""
    # Kill all sessions first
    for s in mcp_server.sessions:
        s.kill()
    result = list_sessions(socket_name=mcp_server.socket_name)
    assert result.items == []
    assert result.total == 0


@pytest.mark.usefixtures("mcp_session")
def test_create_session(mcp_server: Server) -> None:
    """create_session creates a new tmux session."""
    result = create_session(
        session_name="mcp_test_new",
        socket_name=mcp_server.socket_name,
        allow_server_start=True,
    )
    assert result.session_name == "mcp_test_new"
    assert result.session_id is not None
    assert result.server_started is False


def test_create_session_does_not_start_dead_server_by_default(
    TestServer: type[Server],
) -> None:
    """Direct Python calls deny an implicit daemon start by default.

    The standard server fixture is intentionally live, so ``TestServer`` is
    used to create a cleanup-managed libtmux target without a daemon.
    """
    from libtmux_mcp._utils import ExpectedToolError

    server = TestServer()
    assert server.socket_name is not None
    assert server.is_alive() is False

    with pytest.raises(ExpectedToolError, match="No tmux server is running"):
        create_session(
            session_name="mcp_no_implicit_server",
            socket_name=server.socket_name,
        )

    assert server.is_alive() is False


@pytest.mark.parametrize(
    "invalid",
    ["false", 0, 1, None],
    ids=["string", "zero", "one", "none"],
)
def test_create_session_rejects_non_bool_start_permission_before_target_access(
    monkeypatch: pytest.MonkeyPatch,
    invalid: object,
) -> None:
    """Direct calls require an exact bool before inspecting a tmux target.

    Target lookup is replaced because this contract specifically guards the
    validation boundary before any liveness or session-creation work.
    """
    from libtmux_mcp._utils import ExpectedToolError
    from libtmux_mcp.tools import server_tools

    def fail_target_lookup(*args: object, **kwargs: object) -> t.NoReturn:
        pytest.fail(f"target lookup ran with args={args!r}, kwargs={kwargs!r}")

    monkeypatch.setattr(server_tools, "_get_server", fail_target_lookup)

    with pytest.raises(ExpectedToolError, match="allow_server_start must be a bool"):
        create_session(allow_server_start=t.cast("t.Any", invalid))


def test_create_session_denial_survives_server_death_at_new_session(
    TestServer: type[Server],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target dying at command execution cannot be restarted by denial.

    The command hook creates the otherwise unrepeatable liveness race while
    retaining real libtmux and tmux behavior on both sides of the boundary.
    """
    from libtmux import Server

    from libtmux_mcp._utils import ExpectedToolError

    server = TestServer()
    assert server.socket_name is not None
    server.new_session(session_name="mcp_start_race_anchor")
    assert server.is_alive() is True

    original_cmd = Server.cmd
    armed = True

    def kill_before_new_session(
        command_server: Server,
        cmd: str,
        *args: t.Any,
        target: str | int | None = None,
    ) -> t.Any:
        nonlocal armed
        is_new_session = cmd == "new-session" or (
            cmd == "-N" and bool(args) and args[0] == "new-session"
        )
        if armed and is_new_session:
            armed = False
            server.kill()
        return original_cmd(command_server, cmd, *args, target=target)

    monkeypatch.setattr(Server, "cmd", kill_before_new_session)

    with pytest.raises(ExpectedToolError, match="No tmux server is running"):
        create_session(
            session_name="mcp_start_race_denied",
            socket_name=server.socket_name,
            allow_server_start=False,
        )

    assert server.is_alive() is False


def test_concurrent_create_session_attributes_only_startup_path(
    TestServer: type[Server],
) -> None:
    """Only the startup-enabled path reports starting a shared target."""
    server = TestServer()
    assert server.socket_name is not None
    assert server.is_alive() is False
    ready = threading.Barrier(2)

    def create(index: int) -> bool:
        ready.wait()
        result = create_session(
            session_name=f"mcp_concurrent_start_{index}",
            socket_name=server.socket_name,
            allow_server_start=True,
        )
        return result.server_started

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        started = list(executor.map(create, range(2)))

    assert sorted(started) == [False, True]
    assert server.is_alive() is True


def test_create_session_explicit_start_reports_new_daemon(
    TestServer: type[Server],
) -> None:
    """Explicit permission starts a dead server and reports that side effect."""
    server = TestServer()
    assert server.socket_name is not None
    assert server.is_alive() is False

    result = create_session(
        session_name="mcp_explicit_server_start",
        socket_name=server.socket_name,
        allow_server_start=True,
    )

    assert server.is_alive() is True
    assert result.server_started is True


@pytest.mark.parametrize(
    ("enabled", "expect_error"),
    [(False, True), (True, False)],
    ids=["disabled", "enabled"],
)
def test_mcp_create_session_omission_inherits_server_start_default(
    TestServer: type[Server],
    enabled: bool,
    expect_error: bool,
) -> None:
    """Omitted MCP input inherits the transformed startup policy."""
    import asyncio

    from fastmcp import Client, FastMCP

    from libtmux_mcp._server_start import _configure_server_start_default
    from libtmux_mcp.tools import server_tools

    server = TestServer()
    assert server.socket_name is not None
    mcp = FastMCP(f"server-start-{enabled}")
    server_tools.register(mcp)
    _configure_server_start_default(mcp, enabled)

    async def _call() -> t.Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "create_session",
                {
                    "session_name": f"mcp_transformed_start_{enabled}",
                    "socket_name": server.socket_name,
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is expect_error
    assert server.is_alive() is enabled
    if enabled:
        assert result.structured_content is not None
        assert result.structured_content["server_started"] is True
    else:
        assert result.content
        assert "No tmux server is running" in result.content[0].text


def test_mcp_explicit_false_overrides_enabled_server_start_default(
    TestServer: type[Server],
) -> None:
    """Explicit MCP denial wins over the enabled startup default."""
    import asyncio

    from fastmcp import Client, FastMCP

    from libtmux_mcp._server_start import _configure_server_start_default
    from libtmux_mcp.tools import server_tools

    server = TestServer()
    assert server.socket_name is not None
    mcp = FastMCP("server-start-explicit-false")
    server_tools.register(mcp)
    _configure_server_start_default(mcp, True)

    async def _call() -> t.Any:
        async with Client(mcp) as client:
            return await client.call_tool(
                "create_session",
                {
                    "session_name": "mcp_explicit_start_denial",
                    "socket_name": server.socket_name,
                    "allow_server_start": False,
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is True
    assert result.content
    assert "No tmux server is running" in result.content[0].text
    assert server.is_alive() is False


@pytest.mark.usefixtures("mcp_session")
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
    assert any(p.pane_id == result.active_pane_id for p in panes.items)


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
@pytest.mark.usefixtures("mcp_session")
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
        if expected_count is not None:
            assert len(result.items) == expected_count
            assert result.total == expected_count
        else:
            assert len(result.items) >= 1


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
    from libtmux_mcp.models import ServerInfo, SessionInfo, SessionPage

    sessions = list_sessions(socket_name=mcp_server.socket_name)
    assert isinstance(sessions, SessionPage)
    assert all(isinstance(s, SessionInfo) for s in sessions.items)

    info = get_server_info(socket_name=mcp_server.socket_name)
    assert isinstance(info, ServerInfo)


@pytest.mark.usefixtures("mcp_session")
def test_list_servers_finds_live_socket(mcp_server: Server) -> None:
    """``list_servers`` enumerates the current user's tmux sockets.

    The fixture server is a real tmux process with a real socket
    under ``$TMUX_TMPDIR/tmux-$UID/``; the discovery tool must see
    it and report it alive.
    """
    from libtmux_mcp.models import ServerInfo, ServerPage

    results = list_servers()
    assert isinstance(results, ServerPage)
    assert all(isinstance(r, ServerInfo) for r in results.items)
    names = [server.socket_name for server in results.items]
    assert mcp_server.socket_name in names
    # The fixture's socket must be reported alive.
    found = next(
        server
        for server in results.items
        if server.socket_name == mcp_server.socket_name
    )
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
    assert results.items == []
    assert results.total == 0


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

    # Canonical scan saw an empty tmpdir, so everything below came from
    # the extra-paths probe.
    socket_paths = [server.socket_path for server in results.items]
    assert str(fixture_socket) in socket_paths
    found = next(
        server for server in results.items if server.socket_path == str(fixture_socket)
    )
    assert isinstance(found, ServerInfo)
    assert found.is_alive is True


@pytest.mark.usefixtures("mcp_session")
def test_list_servers_deduplicates_canonical_and_extra_socket_path(
    mcp_server: Server,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical and explicit aliases of one socket produce one row."""
    canonical_dir = tmp_path / f"tmux-{os.geteuid()}"
    canonical_dir.mkdir()
    socket_name = mcp_server.socket_name
    assert socket_name is not None
    fixture_socket = pathlib.Path("/tmp") / f"tmux-{os.geteuid()}" / socket_name
    assert fixture_socket.is_socket()
    (canonical_dir / socket_name).symlink_to(fixture_socket)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))

    result = list_servers(extra_socket_paths=[str(fixture_socket)])

    assert result.total == 1
    assert len(result.items) == 1


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
    assert results.items == []
    assert results.total == 0
