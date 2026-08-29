"""Tests for libtmux MCP server tools."""

from __future__ import annotations

import os
import pathlib
import tempfile
import typing as t

import pytest
from fastmcp.exceptions import ToolError

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
    filters: dict[str, str | bool | int] | None
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
        error_match="is not a filter operator",
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
    filters: dict[str, str | bool | int] | None,
    expected_count: int | None,
    expect_error: bool,
    error_match: str | None,
) -> None:
    """list_sessions supports QueryList filtering."""
    from fastmcp.exceptions import ToolError

    if filters is not None:
        session_name = mcp_session.session_name
        assert session_name is not None
        resolved: dict[str, str | bool | int] = {}
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


def test_list_servers_reports_a_complete_identity_and_dedups(
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """Each row carries both identity fields, and extras do not duplicate.

    The scan holds the full path in the directory entry but reported
    only the name, so ``socket_path`` was null on every scanned row.
    Passing the same socket via ``extra_socket_paths`` then listed it a
    second time with the opposite half of its identity, and nothing tied
    the two rows together.
    """
    import os
    import pathlib as _pathlib

    assert mcp_session is not None  # forces the tmux server to exist
    socket_path = (
        _pathlib.Path(os.environ.get("TMUX_TMPDIR", "/tmp"))
        / f"tmux-{os.geteuid()}"
        / str(mcp_server.socket_name)
    )

    scanned = [r for r in list_servers() if r.socket_name == mcp_server.socket_name]
    assert len(scanned) == 1
    assert scanned[0].socket_path == str(socket_path)

    both = list_servers(extra_socket_paths=[str(socket_path)])
    same_server = [
        r
        for r in both
        if r.socket_name == mcp_server.socket_name or r.socket_path == str(socket_path)
    ]
    assert len(same_server) == 1


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

    # Where the fixture's socket actually is, read BEFORE the scan is
    # repointed. Hardcoding /tmp assumed the ambient TMUX_TMPDIR, which
    # stopped being true once the suite isolated it per test.
    fixture_socket = (
        pathlib.Path(os.environ.get("TMUX_TMPDIR", "/tmp"))
        / f"tmux-{os.geteuid()}"
        / (mcp_server.socket_name or "default")
    )
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
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


def test_tools_refuse_a_wedged_server_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that accepts and never answers used to hang every tool.

    ``list_servers`` reports such a socket honestly, so the operator's
    next call is aimed straight at it -- and ``get_server_info``,
    ``list_sessions`` and ``capture_pane`` all blocked forever.
    ``capture_pane`` is the one that shows it was never a
    ``list_servers`` problem: it does not probe liveness at all, it
    resolves a pane through ``Server.cmd``, which has no timeout.

    The bound lives in ``_get_server``, which every tool funnels
    through. A DEAD socket must be unaffected -- it answers immediately,
    which is not a timeout -- so the control matters as much as the
    case.
    """
    import socket as socket_module
    import threading
    import time

    from fastmcp.exceptions import ToolError

    from libtmux_mcp._utils import _server_cache
    from libtmux_mcp.tools.server_tools import get_server_info

    # A short dir, not ``tmp_path``: UNIX socket paths cap at 108 bytes.
    # Isolated from the real TMUX_TMPDIR so a parallel worker's scan
    # cannot see this socket, and so a leak cannot outlive the test.
    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        monkeypatch.setenv("TMUX_TMPDIR", tmpdir)
        uid_dir = pathlib.Path(tmpdir) / f"tmux-{os.geteuid()}"
        # 0700 or tmux refuses the directory outright, which answers
        # instantly and would let this test pass without ever reaching
        # the socket it built.
        uid_dir.mkdir(mode=0o700)
        _server_cache.clear()

        listener = socket_module.socket(
            socket_module.AF_UNIX, socket_module.SOCK_STREAM
        )
        listener.bind(str(uid_dir / "silent"))
        listener.listen(8)
        listener.settimeout(0.2)
        stop = threading.Event()
        held: list[socket_module.socket] = []

        def serve() -> None:
            while not stop.is_set():
                try:
                    conn, _ = listener.accept()
                except (TimeoutError, OSError):
                    continue
                held.append(conn)  # accept, never speak, never close

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            started = time.monotonic()
            with pytest.raises(ToolError, match="did not answer within"):
                get_server_info(socket_name="silent")
            elapsed = time.monotonic() - started

            # Control: a socket with nothing behind it is NOT a timeout.
            # Without it, a guard that refused every socket would pass.
            _server_cache.clear()
            absent = get_server_info(socket_name="absent")
            assert absent.is_alive is False
        finally:
            stop.set()
            listener.close()
            for conn in held:
                conn.close()
            thread.join(timeout=2)
            _server_cache.clear()

    assert elapsed < 15.0, f"the refusal took {elapsed:.1f}s"


def test_list_servers_survives_a_socket_that_never_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener is not a server that replies, and one hung the scan.

    ``_is_tmux_socket_live`` proves only that the connection was
    accepted. A tmux server spinning inside its own event loop does
    exactly that and never answers, and ``Server.cmd`` has no timeout --
    so ONE such socket made ``list_servers`` never return. Measured on a
    real directory: 2.03s before the silent listener was added, and not
    finished 85 seconds after.

    The socket is REPORTED rather than dropped. Dropping it would be the
    same "empty means absent" claim the resource path was fixed for, and
    a wedged server is the thing an operator is looking for.
    """
    import socket as socket_module
    import threading
    import time

    from libtmux_mcp.tools.server_tools import _PROBE_TIMEOUT_SECONDS

    # An EMPTY TMUX_TMPDIR, so the measurement is of this code and not of
    # the machine's socket litter. Unisolated, the scan probes every socket
    # in the shared directory -- 1785 of them on the development box, ~1 ms
    # each when quiet, but the per-probe cost inflates under load: 40.38 s
    # against 0.25 s quiet. The sibling below isolates for the same reason.
    with (
        tempfile.TemporaryDirectory(prefix="lsq-") as empty_dir,
        tempfile.TemporaryDirectory() as tmpdir,
    ):
        monkeypatch.setenv("TMUX_TMPDIR", empty_dir)
        path = pathlib.Path(tmpdir) / "silent.sock"
        listener = socket_module.socket(
            socket_module.AF_UNIX, socket_module.SOCK_STREAM
        )
        listener.bind(str(path))
        listener.listen(8)
        listener.settimeout(0.2)
        stop = threading.Event()
        held: list[socket_module.socket] = []

        def serve() -> None:
            while not stop.is_set():
                try:
                    conn, _ = listener.accept()
                except (TimeoutError, OSError):
                    continue
                held.append(conn)  # accept, never speak, never close

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            started = time.monotonic()
            found = list_servers(extra_socket_paths=[str(path)])
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            listener.close()
            for conn in held:
                conn.close()
            thread.join(timeout=2)

    # Derived from the product's constant, so it moves when that does. One
    # silent socket costs one probe timeout and the multiple is headroom:
    # this is a CEILING a working scan returns from in about 2 s. A literal
    # 10.0 asserts the machine's speed -- it failed at loadavg 90, once by
    # 28 ms, which is a coin flip rather than a caught defect.
    ceiling = _PROBE_TIMEOUT_SECONDS * 5
    assert elapsed < ceiling, (
        f"list_servers took {elapsed:.1f}s against a silent socket "
        f"(ceiling {ceiling:.1f}s)"
    )
    rows = [row for row in found if row.socket_name == "silent.sock"]
    assert rows, "the unreachable socket was dropped instead of reported"
    assert rows[0].is_alive is False
    assert "did not answer" in (rows[0].unreachable_reason or "")


def test_list_servers_is_ordered_and_complete_under_concurrency(
    TestServer: type[Server], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan probes sockets in parallel; order must not depend on timing.

    ``ThreadPoolExecutor.map`` preserves input order, so the listing
    stays sorted by socket name however the probes interleave.

    Not ``tmp_path``: a UNIX socket path is capped at 108 bytes, and
    under ``pytest-xdist`` the worker and test-name components push
    ``<tmp_path>/tmux-<uid>/libtmux_test<rand>`` past it. The failure is
    invisible in a serial run.
    """
    with tempfile.TemporaryDirectory(prefix="lsq-") as short_dir:
        monkeypatch.setenv("TMUX_TMPDIR", short_dir)
        _assert_scan_is_ordered_and_complete(TestServer)


def _assert_scan_is_ordered_and_complete(TestServer: type[Server]) -> None:
    """Body of the scan test, split out to keep the tmpdir scope tight."""
    servers = [TestServer() for _ in range(4)]
    for server in servers:
        server.new_session(session_name="probe")

    listed = list_servers()
    names = [row.socket_name for row in listed if row.socket_name is not None]
    assert names == sorted(names)
    assert len(names) == len(servers)


def test_a_tool_is_bounded_past_the_liveness_probe(
    monkeypatch: pytest.MonkeyPatch,
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """The sibling of the never-answers case, and the one that bit.

    A socket that never answers is caught by the 5s liveness probe, so
    the call *behind* the probe never runs -- that fixture is
    structurally incapable of producing this defect. A socket that
    answers the probe and stalls afterwards reaches it: ``break_pane``
    makes eleven round trips, and every one after the first went
    through libtmux's untimed ``Popen.communicate()``. Measured before
    the fix: still running at 150s.

    ``break_pane`` stands in for the class. The bound is installed once
    at ``tmux_cmd``, so a per-tool test here would say nothing the
    ``tmux_cmd`` tests do not already say.
    """
    import socket as socket_module
    import threading
    import time

    from fastmcp.exceptions import ToolError

    from libtmux_mcp import _utils
    from libtmux_mcp._utils import _server_cache
    from libtmux_mcp.tools.window_tools import break_pane

    # Exercise the mechanism, not the shipped constant: a 5s bound would
    # spend 5s of suite time proving what 0.5s proves.
    monkeypatch.setattr(_utils, "_SYNC_CALL_TIMEOUT_SECONDS", 0.5)
    upstream = (
        pathlib.Path(os.environ.get("TMUX_TMPDIR", "/tmp"))
        / f"tmux-{os.geteuid()}"
        / (mcp_server.socket_name or "default")
    )
    assert upstream.is_socket(), "fixture socket must exist for the test"

    with tempfile.TemporaryDirectory(dir="/tmp") as tmpdir:
        monkeypatch.setenv("TMUX_TMPDIR", tmpdir)
        uid_dir = pathlib.Path(tmpdir) / f"tmux-{os.geteuid()}"
        uid_dir.mkdir(mode=0o700)  # tmux refuses any other mode, instantly
        _server_cache.clear()

        listener = socket_module.socket(
            socket_module.AF_UNIX, socket_module.SOCK_STREAM
        )
        listener.bind(str(uid_dir / "halfwedge"))
        listener.listen(16)
        listener.settimeout(0.2)
        stop = threading.Event()
        held: list[socket_module.socket] = []
        forwarded = 0

        def pump(src: socket_module.socket, dst: socket_module.socket) -> None:
            try:
                while True:
                    chunk = src.recv(65536)
                    if not chunk:
                        break
                    dst.sendall(chunk)
            except OSError:
                pass

        def serve() -> None:
            nonlocal forwarded
            first = True
            while not stop.is_set():
                try:
                    conn, _ = listener.accept()
                except (TimeoutError, OSError):
                    continue
                if not first:
                    held.append(conn)  # accept, never speak, never close
                    continue
                first = False
                up = socket_module.socket(
                    socket_module.AF_UNIX, socket_module.SOCK_STREAM
                )
                up.connect(str(upstream))
                forwarded += 1
                for a, b in ((conn, up), (up, conn)):
                    threading.Thread(target=pump, args=(a, b), daemon=True).start()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        outcome: dict[str, BaseException | None] = {}

        pane_id = mcp_pane.pane_id
        assert pane_id is not None

        def call() -> None:
            try:
                break_pane(pane_id=pane_id, socket_name="halfwedge")
            except BaseException as err:  # noqa: BLE001
                outcome["error"] = err
            else:
                outcome["error"] = None

        worker = threading.Thread(target=call, daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(timeout=20.0)
        elapsed = time.monotonic() - started
        try:
            assert not worker.is_alive(), f"still running after {elapsed:.1f}s"
            assert forwarded == 1, (
                "the probe was never forwarded, so the call behind it never ran"
            )
            error = outcome["error"]
            assert isinstance(error, ToolError)
            assert "did not return within" in str(error)
        finally:
            stop.set()
            listener.close()
            for conn in held:
                conn.close()
            thread.join(timeout=2)
            _server_cache.clear()


def test_kill_server_kills_the_server(TestServer: type[Server]) -> None:
    """The destructive path had no functional test, only tier checks.

    Verified by asking the server rather than by trusting the return
    string: a tool that returned "Server killed successfully" and killed
    nothing would have passed on the message alone.
    """
    doomed = TestServer()
    doomed.new_session(session_name="doomed", window_command="sh")
    assert doomed.is_alive()

    result = kill_server(socket_name=doomed.socket_name)

    assert "killed" in result
    assert not doomed.is_alive()


@pytest.mark.usefixtures("mcp_session")
def test_kill_server_refuses_the_caller_s_own_server(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-kill protection covers the whole server, not just a pane.

    ``$TMUX`` is pointed at the target so the guard sees the caller as
    living on it -- the situation the refusal exists for. The control
    above proves the refusal is not simply a tool that never kills.

    ``mcp_session`` is what BOOTS the daemon; the bare ``mcp_server``
    fixture only constructs an unstarted ``Server``, so asserting it is
    still alive afterwards would fail whether or not the kill happened.

    The two assertions need two different mutations. Disabling the
    guard falsifies the ``raises`` block and never reaches the second
    line; only a guard that raises the right error and kills anyway
    falsifies ``is_alive``. So the liveness check is load-bearing
    rather than belt-and-braces -- it catches a tool that refuses in
    words and kills in fact.
    """
    from libtmux_mcp._utils import _effective_socket_path

    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},1,$0")
    monkeypatch.setenv("TMUX_PANE", "%0")

    with pytest.raises(ToolError, match="Refusing to kill"):
        kill_server(socket_name=mcp_server.socket_name)

    assert mcp_server.is_alive()
