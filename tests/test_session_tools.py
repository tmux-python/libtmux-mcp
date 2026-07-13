"""Tests for libtmux MCP session tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.tools.session_tools import (
    create_window,
    get_session_info,
    kill_session,
    list_windows,
    rename_session,
    select_window,
)
from libtmux_mcp.tools.window_tools import list_panes

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session


def test_list_windows(mcp_server: Server, mcp_session: Session) -> None:
    """list_windows returns a list of WindowInfo models."""
    result = list_windows(
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, list)
    assert len(result) >= 1
    assert result[0].window_id is not None


def test_list_windows_by_id(mcp_server: Server, mcp_session: Session) -> None:
    """list_windows can find session by ID."""
    result = list_windows(
        session_id=mcp_session.session_id,
        socket_name=mcp_server.socket_name,
    )
    assert len(result) >= 1


@pytest.mark.parametrize("explicit_scope", [False, True], ids=["default", "explicit"])
def test_list_windows_server_scope_remains_server_wide(
    mcp_server: Server,
    mcp_session: Session,
    explicit_scope: bool,
) -> None:
    """The default and explicit server scope include every session."""
    second_session = mcp_server.new_session(session_name="list_windows_server_scope")
    kwargs: dict[str, t.Any] = {"socket_name": mcp_server.socket_name}
    if explicit_scope:
        kwargs["scope"] = "server"

    result = list_windows(**kwargs)

    session_ids = {window.session_id for window in result}
    assert mcp_session.session_id in session_ids
    assert second_session.session_id in session_ids


def test_list_windows_caller_session_scope_uses_live_caller_session(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller scope resolves the pane instead of trusting stale TMUX metadata."""
    from libtmux_mcp._utils import _effective_socket_path

    caller_pane = mcp_session.active_pane
    assert caller_pane is not None and caller_pane.pane_id is not None
    caller_window = mcp_session.new_window(window_name="list_windows_caller")
    other_session = mcp_server.new_session(session_name="list_windows_other")
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", caller_pane.pane_id)

    result = list_windows(
        scope="caller_session",
        socket_name=mcp_server.socket_name,
    )

    assert {window.session_id for window in result} == {mcp_session.session_id}
    assert caller_window.window_id in {window.window_id for window in result}
    assert other_session.session_id not in {window.session_id for window in result}


def test_list_windows_caller_scope_uses_effective_server_tmux_binary(
    mcp_server: Server,
    mcp_session: Session,
    mcp_pane: Pane,
    custom_tmux_bin_without_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-session lookup keeps the configured effective tmux binary."""
    from libtmux_mcp._utils import _effective_socket_path

    assert mcp_pane.pane_id is not None
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id)

    result = list_windows(
        scope="caller_session",
        socket_name=mcp_server.socket_name,
    )

    assert custom_tmux_bin_without_path.endswith("configured-tmux")
    assert {window.session_id for window in result.items} == {mcp_session.session_id}


def test_list_windows_caller_scope_reraises_unrelated_live_lookup_error(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller scope does not relabel a live operational error as stale."""
    from libtmux import exc
    from libtmux.pane import Pane

    from libtmux_mcp._utils import _effective_socket_path

    caller_pane = mcp_session.active_pane
    assert caller_pane is not None and caller_pane.pane_id is not None
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", caller_pane.pane_id)

    def fail_lookup(cls: type[Pane], server: Server, pane_id: str) -> Pane:
        del cls, server, pane_id
        msg = "injected live caller-session failure"
        raise exc.LibTmuxException(msg)

    monkeypatch.setattr(Pane, "from_pane_id", classmethod(fail_lookup))

    with pytest.raises(
        ExpectedToolError,
        match="tmux error: injected live caller-session failure",
    ):
        list_windows(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
        )


def test_list_windows_caller_scope_reports_server_death_during_lookup(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller scope uses dead-server recovery when lookup loses its target."""
    from libtmux import exc
    from libtmux.pane import Pane

    from libtmux_mcp._utils import _effective_socket_path

    caller_pane = mcp_session.active_pane
    assert caller_pane is not None and caller_pane.pane_id is not None
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", caller_pane.pane_id)

    def kill_and_fail(cls: type[Pane], server: Server, pane_id: str) -> Pane:
        del cls, pane_id
        server.kill()
        msg = "injected dead caller-session failure"
        raise exc.LibTmuxException(msg)

    monkeypatch.setattr(Pane, "from_pane_id", classmethod(kill_and_fail))

    with pytest.raises(ExpectedToolError, match="No tmux server is running"):
        list_windows(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
        )


@pytest.mark.parametrize(
    ("selector", "value"),
    [("session_name", "dev"), ("session_id", "$1")],
)
def test_list_windows_caller_session_rejects_explicit_selectors(
    mcp_server: Server,
    selector: str,
    value: str,
) -> None:
    """Caller scope cannot be mixed with hierarchy selectors."""
    with pytest.raises(
        ExpectedToolError,
        match=r"scope='caller_session'.*cannot be combined.*scope='server'",
    ):
        list_windows(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
            **{selector: value},
        )


def test_list_windows_caller_session_rejects_outside_tmux(
    mcp_server: Server,
) -> None:
    """Caller scope fails explicitly when the invocation has no caller."""
    with pytest.raises(
        ExpectedToolError,
        match=r"scope='caller_session'.*inside tmux.*scope='server'",
    ):
        list_windows(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
        )


def test_list_windows_caller_session_rejects_effective_socket_mismatch(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller scope never crosses from the effective target to another socket."""
    from libtmux.pane import Pane

    caller_pane = mcp_session.active_pane
    assert caller_pane is not None and caller_pane.pane_id is not None
    monkeypatch.setenv("TMUX", "/tmp/tmux-99999/caller-only,12345,$0")
    monkeypatch.setenv("TMUX_PANE", caller_pane.pane_id)

    def fail_from_env(
        cls: type[Pane], environment: t.Mapping[str, str] | None = None
    ) -> Pane:
        del cls, environment
        pytest.fail("Pane.from_env must not run across a target mismatch")

    monkeypatch.setattr(Pane, "from_env", classmethod(fail_from_env))

    with pytest.raises(
        ExpectedToolError,
        match=r"scope='caller_session'.*socket.*effective.*scope='server'",
    ):
        list_windows(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
        )


@pytest.mark.parametrize(
    "list_tool",
    [
        pytest.param(list_panes, id="list-panes"),
        pytest.param(list_windows, id="list-windows"),
    ],
)
def test_list_tools_caller_session_rejects_stale_caller_pane(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    list_tool: t.Callable[..., t.Any],
) -> None:
    """Caller scope reports recovery when the frozen pane no longer exists."""
    from libtmux_mcp._utils import _effective_socket_path

    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,{mcp_session.session_id}")
    monkeypatch.setenv("TMUX_PANE", "%999999999")

    with pytest.raises(
        ExpectedToolError,
        match=(
            r"scope='caller_session'.*could not resolve the frozen caller pane.*"
            r"scope='server'.*restart from a live tmux pane"
        ),
    ):
        list_tool(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
        )


def test_list_windows_rejects_invalid_scope(mcp_server: Server) -> None:
    """Direct callers cannot widen a misspelled scope to the server."""
    with pytest.raises(
        ExpectedToolError,
        match=r"Invalid scope.*expected.*server.*caller_session",
    ):
        list_windows(
            scope=t.cast("t.Any", "caller"),
            socket_name=mcp_server.socket_name,
        )


def test_get_session_info(mcp_server: Server, mcp_session: Session) -> None:
    """get_session_info returns a SessionInfo for a single session."""
    result = get_session_info(
        session_id=mcp_session.session_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.session_id == mcp_session.session_id
    assert result.session_name == mcp_session.session_name
    assert result.window_count >= 1


def test_get_session_info_by_name(mcp_server: Server, mcp_session: Session) -> None:
    """get_session_info resolves by session_name when no ID is given."""
    assert mcp_session.session_name is not None
    result = get_session_info(
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert result.session_id == mcp_session.session_id


def test_create_window(mcp_server: Server, mcp_session: Session) -> None:
    """create_window creates a new window in a session."""
    result = create_window(
        session_name=mcp_session.session_name,
        window_name="mcp_test_win",
        socket_name=mcp_server.socket_name,
    )
    assert result.window_name == "mcp_test_win"


def test_create_window_returns_active_pane_id(
    mcp_server: Server, mcp_session: Session
) -> None:
    """create_window returns the new window's active pane id."""
    result = create_window(
        session_name=mcp_session.session_name,
        window_name="mcp_active_pane_id",
        socket_name=mcp_server.socket_name,
    )

    assert result.active_pane_id is not None
    assert result.active_pane_id.startswith("%")


def test_create_window_invalid_direction(
    mcp_server: Server, mcp_session: Session
) -> None:
    """create_window raises ToolError on invalid direction."""
    with pytest.raises(ToolError, match="Invalid direction"):
        create_window(
            session_name=mcp_session.session_name,
            window_name="bad_dir",
            direction="sideways",  # type: ignore[arg-type]
            socket_name=mcp_server.socket_name,
        )


def test_rename_session(mcp_server: Server, mcp_session: Session) -> None:
    """rename_session renames an existing session."""
    original_name = mcp_session.session_name
    result = rename_session(
        new_name="mcp_renamed",
        session_name=original_name,
        socket_name=mcp_server.socket_name,
    )
    assert result.session_name == "mcp_renamed"


class ListWindowsFilterFixture(t.NamedTuple):
    """Test fixture for list_windows with filters."""

    test_id: str
    provide_session: bool
    filters: dict[str, str] | None
    expected_min_count: int
    expect_error: bool


LIST_WINDOWS_FILTER_FIXTURES: list[ListWindowsFilterFixture] = [
    ListWindowsFilterFixture(
        test_id="no_filters_scoped",
        provide_session=True,
        filters=None,
        expected_min_count=1,
        expect_error=False,
    ),
    ListWindowsFilterFixture(
        test_id="no_filters_all_sessions",
        provide_session=False,
        filters=None,
        expected_min_count=1,
        expect_error=False,
    ),
    ListWindowsFilterFixture(
        test_id="filter_by_name",
        provide_session=True,
        filters={"window_name": "<window_name>"},
        expected_min_count=1,
        expect_error=False,
    ),
    ListWindowsFilterFixture(
        test_id="filter_by_name_contains",
        provide_session=False,
        filters={"window_name__contains": "<partial_window>"},
        expected_min_count=1,
        expect_error=False,
    ),
    ListWindowsFilterFixture(
        test_id="filter_active",
        provide_session=True,
        filters={"window_active": "1"},
        expected_min_count=1,
        expect_error=False,
    ),
    ListWindowsFilterFixture(
        test_id="invalid_operator",
        provide_session=True,
        filters={"window_name__badop": "test"},
        expected_min_count=0,
        expect_error=True,
    ),
    ListWindowsFilterFixture(
        test_id="cross_session_filter",
        provide_session=False,
        filters={"window_name": "<cross_window_name>"},
        expected_min_count=1,
        expect_error=False,
    ),
]


@pytest.mark.parametrize(
    ListWindowsFilterFixture._fields,
    LIST_WINDOWS_FILTER_FIXTURES,
    ids=[f.test_id for f in LIST_WINDOWS_FILTER_FIXTURES],
)
def test_list_windows_with_filters(
    mcp_server: Server,
    mcp_session: Session,
    test_id: str,
    provide_session: bool,
    filters: dict[str, str] | None,
    expected_min_count: int,
    expect_error: bool,
) -> None:
    """list_windows supports QueryList filtering and scope broadening."""
    # Create a second session with a named window for cross-session tests
    second_session = mcp_server.new_session(session_name="mcp_filter_second")
    cross_win = second_session.new_window(window_name="cross_target_win")

    window = mcp_session.active_window
    window_name = window.window_name
    assert window_name is not None

    if filters is not None:
        resolved: dict[str, str] = {}
        for k, v in filters.items():
            if v == "<window_name>":
                resolved[k] = window_name
            elif v == "<partial_window>":
                resolved[k] = window_name[:3]
            elif v == "<cross_window_name>":
                resolved[k] = "cross_target_win"
            else:
                resolved[k] = v
        filters = resolved

    kwargs: dict[str, t.Any] = {
        "socket_name": mcp_server.socket_name,
        "filters": filters,
    }
    if provide_session:
        kwargs["session_name"] = mcp_session.session_name

    if expect_error:
        with pytest.raises(ToolError, match="Invalid filter operator"):
            list_windows(**kwargs)
    else:
        result = list_windows(**kwargs)
        assert isinstance(result, list)
        assert len(result) >= expected_min_count

    # Cleanup
    cross_win.kill()
    second_session.kill()


# ---------------------------------------------------------------------------
# select_window tests
# ---------------------------------------------------------------------------


def test_select_window_by_id(mcp_server: Server, mcp_session: Session) -> None:
    """select_window focuses a window by ID."""
    win1 = mcp_session.active_window
    mcp_session.new_window(window_name="select_target")

    result = select_window(
        window_id=win1.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == win1.window_id


def test_select_window_by_index(mcp_server: Server, mcp_session: Session) -> None:
    """select_window focuses a window by index."""
    win1 = mcp_session.active_window
    mcp_session.new_window(window_name="select_idx")

    result = select_window(
        window_index=win1.window_index,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == win1.window_id


def test_select_window_direction_next(mcp_server: Server, mcp_session: Session) -> None:
    """select_window navigates to next window."""
    win1 = mcp_session.active_window
    win2 = mcp_session.new_window(window_name="next_win")

    # Make win1 active
    win1.select()
    result = select_window(
        direction="next",
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == win2.window_id


def test_select_window_requires_target(mcp_server: Server) -> None:
    """select_window raises ToolError without target or direction."""
    with pytest.raises(ToolError, match="Provide"):
        select_window(socket_name=mcp_server.socket_name)


def test_select_window_last_on_single_window_session_raises(
    mcp_server: Server, mcp_session: Session
) -> None:
    """select_window last with no prior window must surface the tmux error.

    Regression guard: ``Session.last_window`` on a session that has
    never had a previously-active window raises ``LibTmuxException``
    with tmux's "no last window" stderr; the tool previously discarded
    the return value and returned the unchanged active window as if
    the navigation had worked.
    """
    # The fixture session is freshly created: there is no previously-
    # active window for last-window to jump back to.
    with pytest.raises(ToolError, match="no last window"):
        select_window(
            direction="last",
            session_name=mcp_session.session_name,
            socket_name=mcp_server.socket_name,
        )


def test_kill_session_requires_target(mcp_server: Server) -> None:
    """kill_session refuses to kill without an explicit target."""
    with pytest.raises(ToolError, match="Refusing to kill"):
        kill_session(socket_name=mcp_server.socket_name)


def test_kill_session(mcp_server: Server) -> None:
    """kill_session kills a session."""
    mcp_server.new_session(session_name="mcp_kill_me")
    result = kill_session(
        session_name="mcp_kill_me",
        socket_name=mcp_server.socket_name,
    )
    assert "killed" in result.lower()
    assert not mcp_server.has_session("mcp_kill_me")
