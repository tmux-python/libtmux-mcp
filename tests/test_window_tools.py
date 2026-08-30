"""Tests for libtmux MCP window tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from libtmux_mcp.tools.window_tools import (
    break_pane,
    get_window_info,
    join_pane,
    kill_window,
    list_panes,
    move_window,
    rename_window,
    resize_window,
    select_layout,
    split_window,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
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


def test_split_window_reports_what_it_did_to_the_pane_it_split(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Each split's SOURCE geometry is the next split's input constraint.

    ``size`` names the new pane, and the result described only the new
    pane -- so building three equal columns across 236 needs the
    157-column remainder, and that number was the one thing missing.
    Without it a chain of N splits needs N-1 ``list_panes`` round trips
    to recover a value the server already had, and a caller who does not
    know to make them gets the wrong layout SILENTLY, because every
    individual response was true.
    """
    mcp_session.cmd("resize-window", "-x", "236", "-y", "90")
    socket = mcp_server.socket_name
    first = mcp_session.active_window.active_pane
    assert first is not None

    row = split_window(
        pane_id=first.pane_id, direction="below", size=31, socket_name=socket
    )
    left = split_window(
        pane_id=row.pane_id, direction="right", size=78, socket_name=socket
    )
    assert left.source_pane is not None
    assert left.source_pane.pane_width == "157"

    # Chained off source_pane alone, with no intervening read.
    right = split_window(
        pane_id=left.source_pane.pane_id,
        direction="right",
        size=78,
        socket_name=socket,
    )
    assert right.source_pane is not None
    # The finished layout is readable from the responses alone. ``row``
    # is deliberately NOT used: it is the snapshot from split A and is
    # stale by now, which is the whole reason source_pane has to be
    # re-read rather than echoed back.
    widths = sorted(
        int(pane.pane_width or 0) for pane in (left, right, right.source_pane)
    )
    assert widths == [78, 78, 78], widths

    # Additive: every field a caller already read is still top-level.
    assert right.pane_id is not None
    assert right.pane_width == "78"


def test_split_window_refuses_a_size_tmux_would_silently_clamp(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``size`` names the NEW pane, and the result describes only that.

    Measured on an 80-column pane: 78 is the largest value tmux honours,
    and 79, 80, 120 and 1_000_000 all silently clamp the new pane to 78
    while leaving the source at one column. The caller who asked for 120
    was told 78, with nothing said about their own pane -- a clean
    success report for a broken layout.

    The line is where tmux stops honouring the request, not where the
    layout gets cramped: a faithful split that leaves a narrow source is
    the caller's choice and the report is true.
    """
    mcp_session.cmd("resize-window", "-x", "80", "-y", "24")
    window = mcp_session.active_window
    for bad in (79, 120, 1_000_000, "99%"):
        with pytest.raises(ToolError, match="leaves the pane being split"):
            split_window(
                window_id=window.window_id,
                direction="right",
                size=bad,
                socket_name=mcp_server.socket_name,
            )

    # Controls: the largest honoured size is allowed and the arithmetic
    # holds, and an ordinary split is untouched.
    source = window.active_pane
    assert source is not None
    result = split_window(
        pane_id=source.pane_id,
        direction="right",
        size=40,
        socket_name=mcp_server.socket_name,
    )
    source.refresh()
    assert result.pane_width == "40"
    assert source.pane_width == "39"  # 80 - 40 - 1 for the border


def test_split_window_never_returns_a_pane_that_is_already_gone(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A split whose command cannot run reported success for a dead pane.

    tmux reports the split as successful, the process exits, and the
    pane is removed -- so the caller received a ``PaneInfo`` whose
    ``pane_id`` no longer resolved. Two mechanisms are needed because
    a one-argument command reaches ``$SHELL -c`` rather than exec:
    ``/no/such/shell`` is decidable in advance, while
    ``#{session_name}`` becomes an sh comment that exits 0 and can only
    be caught afterwards.
    """
    window = mcp_session.active_window
    for bad in ("/no/such/shell-xyz", "#{session_name}"):
        with pytest.raises(ToolError) as excinfo:
            split_window(
                window_id=window.window_id,
                shell=bad,
                socket_name=mcp_server.socket_name,
            )
        assert "exited immediately" in str(excinfo.value) or "not an executable" in str(
            excinfo.value
        )

    # Control: a shell one-liner is undecidable in advance and must not
    # be refused. Checking the program alone rejected 'cd /tmp && ...',
    # 'VAR=1 ...' and 'exec ...', all of which tmux runs.
    ok = split_window(
        window_id=window.window_id,
        shell="cd /tmp && sleep 60",
        socket_name=mcp_server.socket_name,
    )
    assert ok.pane_id in [pane.pane_id for pane in window.panes]


def test_split_window_refuses_a_start_directory_tmux_would_ignore(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Tmux falls back to $HOME for an unusable directory, silently.

    ``spawn.c`` tries ``chdir(cwd)``, then ``chdir($HOME)``, then
    ``chdir("/")`` and succeeds either way, so the pane started
    somewhere that was never requested while the result said the split
    worked. An empty string is included deliberately: it is not the
    same as omitting the argument, and used to hand the caller the MCP
    server's own working directory.
    """
    window = mcp_session.active_window
    for bad in ("/no/such/dir/xyz", "-k", ""):
        with pytest.raises(ToolError, match="not a usable directory"):
            split_window(
                window_id=window.window_id,
                start_directory=bad,
                socket_name=mcp_server.socket_name,
            )

    honoured = split_window(
        window_id=window.window_id,
        start_directory="/tmp",
        socket_name=mcp_server.socket_name,
    )
    assert honoured.pane_current_path == "/tmp"


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
        with pytest.raises(ToolError, match="is not a filter operator"):
            list_panes(**kwargs)
    else:
        result = list_panes(**kwargs)
        assert isinstance(result, list)
        assert len(result) >= expected_min_count


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


def test_list_panes_filters_by_is_caller(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filter by is_caller, the workflow the server instructions promise.

    ``is_caller`` is computed during serialization rather than read off
    tmux, so this only works because a tool's own output fields are
    filterable. It is the only documented answer to "which pane am I
    in?" -- there is no whoami tool.
    """
    from libtmux_mcp._caller import _effective_socket_path

    pane = mcp_session.active_window.active_pane
    assert pane is not None and pane.pane_id is not None
    mcp_session.active_window.split()

    socket_path = _effective_socket_path(mcp_server)
    assert socket_path is not None
    monkeypatch.setenv("TMUX", f"{socket_path},1,{mcp_session.session_id or '$0'}")
    monkeypatch.setenv("TMUX_PANE", pane.pane_id)

    # Both forms: MCP clients that respect dict[str, str] send the string.
    probes: list[dict[str, t.Any]] = [{"is_caller": True}, {"is_caller": "true"}]
    for filters in probes:
        result = list_panes(socket_name=mcp_server.socket_name, filters=filters)
        assert [p.pane_id for p in result] == [pane.pane_id]

    # A substring or collection operator on a bool answers every query
    # with an empty list upstream, so it is refused rather than run.
    with pytest.raises(ToolError, match="does not apply to boolean field"):
        list_panes(
            socket_name=mcp_server.socket_name,
            filters={"is_caller__contains": "true"},
        )
    with pytest.raises(ToolError, match="takes a boolean"):
        list_panes(socket_name=mcp_server.socket_name, filters={"is_caller": "ture"})


def test_break_and_join_pane_preserve_the_pane(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Moving a pane must keep the pane, not replace it.

    The alternative to these tools is kill-and-recreate, which loses the
    process, the scrollback and the pane id -- and any cursor a caller
    holds against that id. Both report the location re-read after the
    move rather than the one that was requested.
    """
    window = mcp_session.active_window
    pane = window.split()
    assert pane.pane_id is not None
    other = mcp_session.new_window()
    assert other.window_id is not None

    broken = break_pane(
        pane_id=pane.pane_id,
        window_name="broken-out",
        socket_name=mcp_server.socket_name,
    )
    assert broken.window_name == "broken-out"
    assert broken.window_id != window.window_id

    joined = join_pane(
        pane_id=pane.pane_id,
        target_window_id=other.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert joined.pane.pane_id == pane.pane_id
    assert joined.pane.window_id == other.window_id
    # The pane was its source window's last, so tmux removed that
    # window. Consolidating panes deletes windows the caller never
    # named, and the result has to say so.
    assert joined.source_window_id == broken.window_id
    assert joined.source_window_destroyed is True


def test_break_pane_refuses_to_empty_its_source_session(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Breaking the last pane of the last window destroys the session.

    tmux puts the new window in the CURRENT session, which need not be
    the pane's own. When the pane was its session's last, that session
    is left with no windows and tmux destroys it -- measured, breaking
    alpha's only pane moved it to beta and alpha ceased to exist, while
    the result reported only where the pane went.

    Destroying a session is destructive-tier work and this tool is
    mutating, so it refuses rather than discloses.
    """
    other = mcp_server.new_session(session_name="break_target")
    window = mcp_session.active_window
    pane = window.active_pane
    assert pane is not None and pane.pane_id is not None

    with pytest.raises(ToolError, match="would leave that session with no windows"):
        break_pane(pane_id=pane.pane_id, socket_name=mcp_server.socket_name)

    names = [s.session_name for s in mcp_server.sessions]
    assert mcp_session.session_name in names
    assert other.session_name in names

    # A session with another window is not at risk, so the move proceeds.
    mcp_session.new_window()
    moved = break_pane(pane_id=pane.pane_id, socket_name=mcp_server.socket_name)
    assert moved.window_id is not None


@pytest.mark.parametrize(
    ("tmux_version", "expected_args"),
    [
        pytest.param(
            "3.6",
            ("-d", "-n", "#(literal-name)", "-s"),
            id="ordinary-release",
        ),
        pytest.param("3.7", ("-d", "-n", "libtmux", "-s"), id="tmux-3.7"),
    ],
)
def test_break_pane_builds_a_version_safe_command(
    monkeypatch: pytest.MonkeyPatch,
    mcp_server: Server,
    mcp_session: Session,
    tmux_version: str,
    expected_args: tuple[str, ...],
) -> None:
    """Probe exact 3.7 and failure output without moving a pane."""
    pane = mcp_session.active_window.active_pane
    assert pane is not None and pane.pane_id is not None
    mcp_session.new_window()

    class _Command(t.NamedTuple):
        returncode: int
        stdout: list[str]
        stderr: list[str]

    captured: list[tuple[str, ...]] = []

    def fail_break_pane(command: str, *args: str, **_kwargs: t.Any) -> _Command:
        captured.append((command, *args))
        return _Command(1, [], [])

    monkeypatch.setattr(
        "libtmux_mcp.tools.window_tools.get_version_str",
        lambda **_kwargs: tmux_version,
    )
    monkeypatch.setattr(mcp_server, "cmd", fail_break_pane)

    with pytest.raises(ToolError, match="pane state may have changed"):
        break_pane(
            pane_id=pane.pane_id,
            window_name="#(literal-name)",
            socket_name=mcp_server.socket_name,
        )

    assert captured == [("break-pane", *expected_args, pane.pane_id)]


def test_move_window_refuses_to_empty_its_source_session(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Moving a session's last window elsewhere destroys that session.

    Same shape as ``break_pane``, and found by auditing which
    ``mutating``-tier tools can reduce an object count rather than by
    tripping over it: moving alpha's only window to beta made alpha
    cease to exist while the result named only the destination.
    """
    other = mcp_server.new_session(session_name="move_target")
    window = mcp_session.active_window

    with pytest.raises(ToolError, match="would leave that one empty"):
        move_window(
            window_id=window.window_id,
            destination_session=other.session_name,
            socket_name=mcp_server.socket_name,
        )

    names = [s.session_name for s in mcp_server.sessions]
    assert mcp_session.session_name in names

    # A session with another window is not at risk, so the move proceeds.
    mcp_session.new_window()
    moved = move_window(
        window_id=window.window_id,
        destination_session=other.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert moved.session_name == other.session_name


def test_join_pane_refuses_to_empty_its_source_session(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A mutating-tier tool must not be able to destroy a session.

    ``break_pane`` already refused exactly this predicate -- only pane,
    only window, of a session -- and its sibling reached the same end
    state with no check, so a client restricted to ``mutating`` could
    destroy sessions and, on a single-session server, the server.

    A window emptying is inherent to moving its last pane and is
    disclosed rather than refused. A session emptying is avoidable: add
    a window first.
    """
    other = mcp_server.new_session(session_name="join_target")
    window = mcp_session.active_window
    pane = window.active_pane
    assert pane is not None and pane.pane_id is not None
    target = other.active_window
    assert target.window_id is not None

    with pytest.raises(ToolError, match="would leave that session with no windows"):
        join_pane(
            pane_id=pane.pane_id,
            target_window_id=target.window_id,
            socket_name=mcp_server.socket_name,
        )
    assert mcp_session.session_name in [s.session_name for s in mcp_server.sessions]

    # Within the same session the source cannot be emptied, so it runs.
    inner = mcp_session.new_window()
    assert inner.window_id is not None
    moved = join_pane(
        pane_id=pane.pane_id,
        target_window_id=inner.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert moved.pane.pane_id == pane.pane_id


def test_kill_tools_disclose_what_went_with_the_target(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Killing the last child takes its parent, and the result says so.

    The destructive tier permits the cascade; the bare "Window killed:
    @0" understated it. An agent tidying up a window has no reason to
    expect the session to go with it, and on a single-session server
    killing a session exits tmux entirely.
    """
    other = mcp_server.new_session(session_name="kill_probe")
    window = mcp_session.active_window
    assert window.window_id is not None

    # Not the last window, so no cascade.
    extra = mcp_session.new_window()
    assert extra.window_id is not None
    plain = kill_window(window_id=extra.window_id, socket_name=mcp_server.socket_name)
    assert "is gone" not in plain

    # The session's last window takes the session.
    cascaded = kill_window(
        window_id=window.window_id, socket_name=mcp_server.socket_name
    )
    assert mcp_session.session_name is not None
    assert mcp_session.session_name in cascaded
    assert "is gone" in cascaded
    assert other.session_name in [s.session_name for s in mcp_server.sessions]


def test_filters_validate_the_same_through_both_transports() -> None:
    """The dict and JSON-string forms are documented as equivalent.

    They were not. The dict branch validated strictly as
    ``dict[str, str]`` so a bool value was rejected, while the string
    branch was ``json.loads``-ed into a dict holding a real bool that
    nothing re-checked. So ``{"is_caller": true}`` -- the description's
    own answer to "which pane am I in?", and the only mechanism for it
    since there is deliberately no whoami tool -- worked or failed
    depending on how the client serialised the argument.
    """
    import asyncio
    import json

    from fastmcp import FastMCP

    from libtmux_mcp.tools import register_tools

    mcp = FastMCP("filters-transport")
    register_tools(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    schema = tools["list_panes"].parameters["properties"]["filters"]

    async def _validates(value: object) -> bool:
        async with Client(mcp) as client:
            try:
                await client.call_tool("list_panes", {"filters": value})
            except Exception as err:  # noqa: BLE001
                return "validation error" not in str(err)
            return True

    for value in (
        {"is_caller": True},
        {"is_caller": "true"},
        json.dumps({"is_caller": True}),
        json.dumps({"is_caller": "true"}),
    ):
        assert asyncio.run(_validates(value)), f"rejected {value!r}"

    assert schema, "filters must stay a declared parameter for this to mean anything"


def test_a_typed_filter_matches_what_its_string_form_matches(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Accepting a type is not the same as comparing it.

    Every tmux-derived attribute is a STRING -- ``pane_width`` is
    ``"80"``, ``pane_active`` is ``"1"`` -- so widening the schema
    without coercing at the comparison boundary turned a validation
    error into a confident empty result. That is the worse of the two:
    an agent filtering for pane 0 is told there is no such pane rather
    than that it passed the wrong type.

    ``is_caller`` is deliberately NOT the field under test here, and
    that is the point: it is computed in Python as a real bool, so it
    is the one field where a bool works without any coercion. A test
    holding only that field passes straight over this regression, which
    is what the first version of this test did. It cannot join the
    table either -- outside a real tmux caller it is ``None`` for every
    pane, so both encodings would match nothing and agree vacuously.
    Its transports are covered by the test above.

    Asserts identical RESULT SETS per row. Validation is what passed
    while matching was broken, and every row asserts its string form
    matched something so a vacuous pair cannot read as agreement.
    """
    from libtmux_mcp.tools.server_tools import list_sessions
    from libtmux_mcp.tools.session_tools import list_windows
    from libtmux_mcp.tools.window_tools import list_panes

    window = mcp_pane.window
    window.split(attach=False)
    socket_name = mcp_server.socket_name
    window_index = window.window_index or "0"
    # Read back rather than off the Pane object: splitting the window
    # changes pane geometry, and the cached attribute is whatever it was
    # when the object was built. A stale width matches nothing, which
    # trips the "matched nothing" guard below rather than the assertion
    # the test is about.
    pane_width = list_panes(socket_name=socket_name)[0].pane_width or "80"

    table: list[tuple[t.Callable[..., t.Any], dict[str, t.Any], dict[str, t.Any]]] = [
        (list_panes, {"pane_width": int(pane_width)}, {"pane_width": pane_width}),
        (list_panes, {"pane_active": True}, {"pane_active": "1"}),
        (
            list_windows,
            {"window_index": int(window_index)},
            {"window_index": window_index},
        ),
        (list_sessions, {"session_windows": 1}, {"session_windows": "1"}),
    ]

    def _ids(rows: list[t.Any]) -> set[str]:
        # Identity, not whole models. The two encodings are necessarily
        # two separate calls, and any live object's attributes can change
        # between them -- a window elsewhere on the server renaming
        # itself made this fail on a difference that has nothing to do
        # with filtering. Which ROWS matched is the property.
        for attr in ("pane_id", "window_id", "session_id"):
            if rows and hasattr(rows[0], attr):
                return {getattr(row, attr) for row in rows}
        msg = "no identity field on the returned rows"
        raise AssertionError(msg)

    for fn, typed, text in table:
        got = fn(filters=typed, socket_name=socket_name)
        want = fn(filters=text, socket_name=socket_name)
        assert want, f"{fn.__name__}{text} matched nothing; the row proves nothing"
        assert _ids(got) == _ids(want), (
            f"{fn.__name__}: {typed} matched {len(got)}, {text} matched {len(want)}"
        )
