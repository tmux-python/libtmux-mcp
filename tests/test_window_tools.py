"""Tests for libtmux MCP window tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.tools.window_tools import (
    get_window_info,
    kill_window,
    list_panes,
    move_window,
    rename_window,
    resize_window,
    select_layout,
    split_window,
)

if t.TYPE_CHECKING:
    from libtmux.server import Server
    from libtmux.session import Session


def test_list_panes(mcp_server: Server, mcp_session: Session) -> None:
    """list_panes returns a list of PaneInfo models."""
    window = mcp_session.active_window
    result = list_panes(
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, list)
    assert len(result) >= 1
    assert result[0].pane_id is not None


@pytest.mark.parametrize("explicit_scope", [False, True], ids=["default", "explicit"])
def test_list_panes_server_scope_remains_server_wide(
    mcp_server: Server,
    mcp_session: Session,
    explicit_scope: bool,
) -> None:
    """The default and explicit server scope include every session."""
    second_session = mcp_server.new_session(session_name="list_panes_server_scope")
    kwargs: dict[str, t.Any] = {"socket_name": mcp_server.socket_name}
    if explicit_scope:
        kwargs["scope"] = "server"

    result = list_panes(**kwargs)

    session_ids = {pane.session_id for pane in result}
    assert mcp_session.session_id in session_ids
    assert second_session.session_id in session_ids


def test_list_panes_caller_session_scope_uses_live_caller_session(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller scope resolves the pane instead of trusting stale TMUX metadata."""
    from libtmux_mcp._utils import _effective_socket_path

    caller_pane = mcp_session.active_pane
    assert caller_pane is not None and caller_pane.pane_id is not None
    mcp_session.new_window(window_name="list_panes_caller_window")
    other_session = mcp_server.new_session(session_name="list_panes_other_session")
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},12345,$stale")
    monkeypatch.setenv("TMUX_PANE", caller_pane.pane_id)

    result = list_panes(
        scope="caller_session",
        socket_name=mcp_server.socket_name,
    )

    assert result
    assert {pane.session_id for pane in result} == {mcp_session.session_id}
    assert other_session.session_id not in {pane.session_id for pane in result}


@pytest.mark.parametrize(
    ("selector", "value"),
    [
        ("session_name", "dev"),
        ("session_id", "$1"),
        ("window_id", "@1"),
        ("window_index", "1"),
    ],
)
def test_list_panes_caller_session_rejects_explicit_selectors(
    mcp_server: Server,
    selector: str,
    value: str,
) -> None:
    """Caller scope cannot be mixed with hierarchy selectors."""
    with pytest.raises(
        ExpectedToolError,
        match=r"scope='caller_session'.*cannot be combined.*scope='server'",
    ):
        list_panes(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
            **{selector: value},
        )


def test_list_panes_caller_session_rejects_outside_tmux(
    mcp_server: Server,
) -> None:
    """Caller scope fails explicitly when the invocation has no caller."""
    with pytest.raises(
        ExpectedToolError,
        match=r"scope='caller_session'.*inside tmux.*scope='server'",
    ):
        list_panes(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
        )


def test_list_panes_caller_session_rejects_effective_socket_mismatch(
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
        list_panes(
            scope="caller_session",
            socket_name=mcp_server.socket_name,
        )


def test_list_panes_rejects_invalid_scope(mcp_server: Server) -> None:
    """Direct callers cannot widen a misspelled scope to the server."""
    with pytest.raises(
        ExpectedToolError,
        match=r"Invalid scope.*expected.*server.*caller_session",
    ):
        list_panes(
            scope=t.cast("t.Any", "caller"),
            socket_name=mcp_server.socket_name,
        )


def test_get_window_info(mcp_server: Server, mcp_session: Session) -> None:
    """get_window_info returns a WindowInfo for a single window."""
    window = mcp_session.active_window
    result = get_window_info(
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == window.window_id
    assert result.window_name is not None
    assert result.pane_count >= 1
    assert result.session_id == mcp_session.session_id


def test_get_window_info_returns_active_pane_id(
    mcp_server: Server, mcp_session: Session
) -> None:
    """get_window_info returns the window's active pane id."""
    window = mcp_session.active_window
    result = get_window_info(
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    active_pane = window.active_pane

    assert result.active_pane_id is not None
    assert active_pane is not None
    assert result.active_pane_id == active_pane.pane_id


def test_get_window_info_by_index(mcp_server: Server, mcp_session: Session) -> None:
    """get_window_info resolves by window_index when session is named."""
    window = mcp_session.active_window
    assert window.window_index is not None
    result = get_window_info(
        window_index=window.window_index,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == window.window_id


def test_split_window(mcp_server: Server, mcp_session: Session) -> None:
    """split_window creates a new pane."""
    window = mcp_session.active_window
    initial_pane_count = len(window.panes)
    result = split_window(
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id is not None
    assert len(window.panes) == initial_pane_count + 1


def test_split_window_with_direction(mcp_server: Server, mcp_session: Session) -> None:
    """split_window respects direction parameter."""
    window = mcp_session.active_window
    result = split_window(
        window_id=window.window_id,
        direction="right",
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id is not None


def test_split_window_invalid_direction(
    mcp_server: Server, mcp_session: Session
) -> None:
    """split_window raises ToolError on invalid direction."""
    window = mcp_session.active_window
    with pytest.raises(ToolError, match="Invalid direction"):
        split_window(
            window_id=window.window_id,
            direction="diagonal",  # type: ignore[arg-type]
            socket_name=mcp_server.socket_name,
        )


def test_rename_window(mcp_server: Server, mcp_session: Session) -> None:
    """rename_window renames a window."""
    window = mcp_session.active_window
    result = rename_window(
        new_name="mcp_renamed_win",
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_name == "mcp_renamed_win"


def test_select_layout(mcp_server: Server, mcp_session: Session) -> None:
    """select_layout changes window layout."""
    window = mcp_session.active_window
    window.split()
    result = select_layout(
        layout="even-horizontal",
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id is not None


def test_resize_window(mcp_server: Server, mcp_session: Session) -> None:
    """resize_window resizes a window."""
    window = mcp_session.active_window
    result = resize_window(
        window_id=window.window_id,
        height=20,
        width=60,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == window.window_id


class ListPanesFilterFixture(t.NamedTuple):
    """Test fixture for list_panes with filters."""

    test_id: str
    scope: str  # "window", "session", "server"
    filters: dict[str, str] | None
    expected_min_count: int
    expect_error: bool


LIST_PANES_FILTER_FIXTURES: list[ListPanesFilterFixture] = [
    ListPanesFilterFixture(
        test_id="window_scope_no_filter",
        scope="window",
        filters=None,
        expected_min_count=1,
        expect_error=False,
    ),
    ListPanesFilterFixture(
        test_id="session_scope_no_filter",
        scope="session",
        filters=None,
        expected_min_count=1,
        expect_error=False,
    ),
    ListPanesFilterFixture(
        test_id="server_scope_no_filter",
        scope="server",
        filters=None,
        expected_min_count=1,
        expect_error=False,
    ),
    ListPanesFilterFixture(
        test_id="filter_active_pane",
        scope="window",
        filters={"pane_active": "1"},
        expected_min_count=1,
        expect_error=False,
    ),
    ListPanesFilterFixture(
        test_id="filter_by_command_contains",
        scope="server",
        filters={"pane_current_command__regex": ".*"},
        expected_min_count=1,
        expect_error=False,
    ),
    ListPanesFilterFixture(
        test_id="invalid_operator",
        scope="window",
        filters={"pane_id__badop": "test"},
        expected_min_count=0,
        expect_error=True,
    ),
    ListPanesFilterFixture(
        test_id="session_scope_with_filter",
        scope="session",
        filters={"pane_active": "1"},
        expected_min_count=1,
        expect_error=False,
    ),
]


@pytest.mark.parametrize(
    ListPanesFilterFixture._fields,
    LIST_PANES_FILTER_FIXTURES,
    ids=[f.test_id for f in LIST_PANES_FILTER_FIXTURES],
)
def test_list_panes_with_filters(
    mcp_server: Server,
    mcp_session: Session,
    test_id: str,
    scope: str,
    filters: dict[str, str] | None,
    expected_min_count: int,
    expect_error: bool,
) -> None:
    """list_panes supports QueryList filtering and scope broadening."""
    window = mcp_session.active_window

    kwargs: dict[str, t.Any] = {
        "socket_name": mcp_server.socket_name,
        "filters": filters,
    }
    if scope == "window":
        kwargs["window_id"] = window.window_id
    elif scope == "session":
        kwargs["session_name"] = mcp_session.session_name

    if expect_error:
        with pytest.raises(ToolError, match="Invalid filter operator"):
            list_panes(**kwargs)
    else:
        result = list_panes(**kwargs)
        assert isinstance(result, list)
        assert len(result) >= expected_min_count


@pytest.mark.parametrize(
    ("filters", "expected_is_caller"),
    [
        ({"is_caller": True}, True),
        ('{"is_caller": true}', True),
        ({"is_caller__exact": True}, True),
        ({"is_caller": False}, False),
        ('{"is_caller": false}', False),
    ],
    ids=["true", "json-true", "exact", "false", "json-false"],
)
def test_list_panes_filters_by_caller(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    filters: dict[str, t.Any] | str,
    expected_is_caller: bool,
) -> None:
    """list_panes filters its serialized caller annotation."""
    from libtmux_mcp._utils import _effective_socket_path

    window = mcp_session.active_window
    caller_pane = window.active_pane
    assert caller_pane is not None and caller_pane.pane_id is not None
    window.split()
    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},1,{mcp_session.session_id or '$0'}")
    monkeypatch.setenv("TMUX_PANE", caller_pane.pane_id)

    result = list_panes(
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
        filters=filters,
    )

    assert result
    assert all(pane.is_caller is expected_is_caller for pane in result)
    result_ids = {pane.pane_id for pane in result}
    if expected_is_caller:
        assert result_ids == {caller_pane.pane_id}
    else:
        assert caller_pane.pane_id not in result_ids


@pytest.mark.parametrize(
    ("filters", "error_match"),
    [
        ({"is_caller": "true"}, "is_caller.*boolean"),
        ({"is_caller__contains": True}, "is_caller.*operator 'contains'"),
    ],
    ids=["non-boolean", "unsupported-operator"],
)
def test_list_panes_rejects_invalid_caller_filters(
    mcp_server: Server,
    mcp_session: Session,
    filters: dict[str, t.Any],
    error_match: str,
) -> None:
    """list_panes rejects invalid caller-filter values and operators."""
    with pytest.raises(ExpectedToolError, match=error_match):
        list_panes(
            window_id=mcp_session.active_window.window_id,
            socket_name=mcp_server.socket_name,
            filters=filters,
        )


# ---------------------------------------------------------------------------
# move_window tests
# ---------------------------------------------------------------------------


def test_move_window_reorder(mcp_server: Server, mcp_session: Session) -> None:
    """move_window changes a window's index."""
    win = mcp_session.new_window(window_name="move_me")
    result = move_window(
        window_id=win.window_id,
        destination_index="99",
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == win.window_id
    assert result.window_index == "99"


def test_move_window_to_another_session(
    mcp_server: Server, mcp_session: Session
) -> None:
    """move_window moves a window to a different session."""
    target_session = mcp_server.new_session(session_name="move_target")
    win = mcp_session.new_window(window_name="move_cross")
    window_id = win.window_id

    result = move_window(
        window_id=window_id,
        destination_session=target_session.session_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == window_id
    # Proof the move actually happened: the returned session_id matches
    # the destination, and the window no longer lives in the source.
    assert result.session_id == target_session.session_id
    source_window_ids = {w.window_id for w in mcp_session.windows}
    assert window_id not in source_window_ids

    # Cleanup
    target_session.kill()


def test_move_window_to_another_session_with_index(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Cross-session move with an explicit destination_index refreshes metadata.

    libtmux's Window.move_window skips its own refresh when BOTH a
    non-empty destination index and a target session are provided. The
    tool must refresh explicitly, otherwise the returned session_id
    would be the pre-move (source) value.
    """
    target_session = mcp_server.new_session(session_name="move_target_indexed")
    win = mcp_session.new_window(window_name="move_cross_idx")
    window_id = win.window_id

    result = move_window(
        window_id=window_id,
        destination_index="7",
        destination_session=target_session.session_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_id == window_id
    assert result.window_index == "7"
    assert result.session_id == target_session.session_id

    target_session.kill()


def test_kill_window_requires_window_id(mcp_server: Server) -> None:
    """kill_window requires window_id as a positional argument."""
    with pytest.raises(ToolError, match="missing 1 required positional argument"):
        kill_window(socket_name=mcp_server.socket_name)  # type: ignore[call-arg]


def test_kill_window(mcp_server: Server, mcp_session: Session) -> None:
    """kill_window kills a window."""
    new_window = mcp_session.new_window(window_name="mcp_kill_win")
    window_id = new_window.window_id
    assert window_id is not None
    result = kill_window(
        window_id=window_id,
        socket_name=mcp_server.socket_name,
    )
    assert "killed" in result.lower()
