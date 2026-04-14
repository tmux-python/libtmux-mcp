"""Tests for libtmux MCP pane tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError
from libtmux import exc as libtmux_exc
from libtmux.test.retry import retry_until

from libtmux_mcp.models import (
    ContentChangeResult,
    PaneContentMatch,
    PaneSnapshot,
    WaitForTextResult,
)
from libtmux_mcp.tools.pane_tools import (
    capture_pane,
    clear_pane,
    display_message,
    enter_copy_mode,
    exit_copy_mode,
    get_pane_info,
    kill_pane,
    paste_text,
    pipe_pane,
    resize_pane,
    search_panes,
    select_pane,
    send_keys,
    set_pane_title,
    snapshot_pane,
    swap_pane,
    wait_for_content_change,
    wait_for_text,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session


def test_send_keys(mcp_server: Server, mcp_pane: Pane) -> None:
    """send_keys sends keys to a pane."""
    result = send_keys(
        keys="echo hello_mcp",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "sent" in result.lower()


def test_capture_pane(mcp_server: Server, mcp_pane: Pane) -> None:
    """capture_pane returns pane content."""
    result = capture_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, str)


def test_capture_pane_untruncated_short_output(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Short output below ``max_lines`` passes through without a header."""
    result = capture_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=100,
        socket_name=mcp_server.socket_name,
    )
    assert "[... truncated" not in result


def test_capture_pane_truncates_tail_preserving(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Long captures are truncated head-first; tail is preserved.

    Prime the pane with >20 echo lines and confirm the last one is
    visible, then capture the visible pane with a tight ``max_lines=5``
    ceiling. The capture must (a) start with a single
    ``[... truncated K lines ...]`` header, (b) have exactly 6 lines
    total (the header + 5 kept lines), and (c) preserve the most
    recent ``scrollback_line_19`` line at the tail.
    """
    for i in range(20):
        mcp_pane.send_keys(f"echo scrollback_line_{i}", enter=True)
    retry_until(
        lambda: "scrollback_line_19" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = capture_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=5,
        socket_name=mcp_server.socket_name,
    )
    lines = result.split("\n")
    assert lines[0].startswith("[... truncated ")
    assert lines[0].endswith(" lines ...]")
    assert len(lines) == 6  # header + exactly 5 preserved tail lines
    assert "scrollback_line_19" in lines[-1] or any(
        "scrollback_line_19" in line for line in lines[1:]
    )


def test_capture_pane_max_lines_none_disables_truncation(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``max_lines=None`` opts out of truncation entirely."""
    for i in range(20):
        mcp_pane.send_keys(f"echo untrunc_line_{i}", enter=True)
    retry_until(
        lambda: "untrunc_line_19" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = capture_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=None,
        socket_name=mcp_server.socket_name,
    )
    assert "[... truncated" not in result
    assert "untrunc_line_19" in result


def test_get_pane_info(mcp_server: Server, mcp_pane: Pane) -> None:
    """get_pane_info returns detailed pane info."""
    result = get_pane_info(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == mcp_pane.pane_id
    assert result.pane_width is not None
    assert result.pane_height is not None


def test_set_pane_title(mcp_server: Server, mcp_pane: Pane) -> None:
    """set_pane_title sets the pane title."""
    result = set_pane_title(
        title="my_test_title",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == mcp_pane.pane_id


def test_clear_pane(mcp_server: Server, mcp_pane: Pane) -> None:
    """clear_pane resets terminal and clears scrollback history."""
    marker = "CLEAR_PANE_MARKER_xyz789"
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = clear_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "cleared" in result.lower()

    # After reset + clear-history, the marker should be gone from scrollback
    retry_until(
        lambda: marker not in "\n".join(mcp_pane.capture_pane(start=-200, end=-1)),
        2,
        raises=True,
    )


def test_resize_pane_dimensions(mcp_server: Server, mcp_pane: Pane) -> None:
    """resize_pane resizes a pane with height/width."""
    result = resize_pane(
        pane_id=mcp_pane.pane_id,
        height=10,
        width=40,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == mcp_pane.pane_id


def test_resize_pane_zoom(mcp_server: Server, mcp_session: Session) -> None:
    """resize_pane zooms a pane."""
    window = mcp_session.active_window
    window.split()
    pane = window.active_pane
    assert pane is not None
    result = resize_pane(
        pane_id=pane.pane_id,
        zoom=True,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane.pane_id


def test_resize_pane_zoom_mutual_exclusivity(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """resize_pane raises ToolError when zoom combined with dimensions."""
    with pytest.raises(ToolError, match="Cannot combine zoom"):
        resize_pane(
            pane_id=mcp_pane.pane_id,
            zoom=True,
            height=10,
            socket_name=mcp_server.socket_name,
        )


def test_kill_pane_requires_pane_id(mcp_server: Server) -> None:
    """kill_pane requires pane_id as a positional argument."""
    with pytest.raises(ToolError, match="missing 1 required positional argument"):
        kill_pane(socket_name=mcp_server.socket_name)  # type: ignore[call-arg]


def test_kill_pane(mcp_server: Server, mcp_session: Session) -> None:
    """kill_pane kills a pane."""
    window = mcp_session.active_window
    new_pane = window.split()
    pane_id = new_pane.pane_id
    assert pane_id is not None
    result = kill_pane(
        pane_id=pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "killed" in result.lower()


# ---------------------------------------------------------------------------
# search_panes tests
# ---------------------------------------------------------------------------


class SearchPanesFixture(t.NamedTuple):
    """Test fixture for search_panes."""

    test_id: str
    command: str
    pattern: str
    regex: bool
    match_case: bool
    scope_to_session: bool
    expected_match: bool
    expected_min_lines: int


SEARCH_PANES_FIXTURES: list[SearchPanesFixture] = [
    SearchPanesFixture(
        test_id="simple_match",
        command="echo FINDME_unique_string_12345",
        pattern="FINDME_unique_string_12345",
        regex=False,
        match_case=False,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="case_insensitive_match",
        command="echo UPPERCASE_findme_test",
        pattern="uppercase_findme_test",
        regex=False,
        match_case=False,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="case_sensitive_no_match",
        command="echo CaseSensitiveTest",
        pattern="casesensitivetest",
        regex=False,
        match_case=True,
        scope_to_session=False,
        expected_match=False,
        expected_min_lines=0,
    ),
    SearchPanesFixture(
        test_id="case_sensitive_match",
        command="echo CaseSensitiveExact",
        pattern="CaseSensitiveExact",
        regex=False,
        match_case=True,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="regex_pattern",
        command="echo error_code_42_found",
        pattern=r"error_code_\d+_found",
        regex=True,
        match_case=False,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="no_match",
        command="echo nothing_special",
        pattern="XYZZY_nonexistent_pattern_99999",
        regex=False,
        match_case=False,
        scope_to_session=False,
        expected_match=False,
        expected_min_lines=0,
    ),
    SearchPanesFixture(
        test_id="scoped_to_session",
        command="echo session_scoped_marker",
        pattern="session_scoped_marker",
        regex=False,
        match_case=False,
        scope_to_session=True,
        expected_match=True,
        expected_min_lines=1,
    ),
]


@pytest.mark.parametrize(
    SearchPanesFixture._fields,
    SEARCH_PANES_FIXTURES,
    ids=[f.test_id for f in SEARCH_PANES_FIXTURES],
)
def test_search_panes(
    mcp_server: Server,
    mcp_session: Session,
    mcp_pane: Pane,
    test_id: str,
    command: str,
    pattern: str,
    regex: bool,
    match_case: bool,
    scope_to_session: bool,
    expected_match: bool,
    expected_min_lines: int,
) -> None:
    """search_panes finds text in pane contents."""
    # Extract the echoed text from the command for polling
    echo_marker = command.split("echo ", 1)[1] if "echo " in command else command
    mcp_pane.send_keys(command, enter=True)
    retry_until(
        lambda: echo_marker in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    kwargs: dict[str, t.Any] = {
        "pattern": pattern,
        "regex": regex,
        "match_case": match_case,
        "socket_name": mcp_server.socket_name,
    }
    if scope_to_session:
        kwargs["session_name"] = mcp_session.session_name

    result = search_panes(**kwargs)
    assert isinstance(result, list)

    if expected_match:
        assert len(result) >= 1
        match = next((r for r in result if r.pane_id == mcp_pane.pane_id), None)
        assert match is not None
        assert len(match.matched_lines) >= expected_min_lines
        assert match.session_id is not None
        assert match.window_id is not None
    else:
        pane_matches = [r for r in result if r.pane_id == mcp_pane.pane_id]
        assert len(pane_matches) == 0


def test_search_panes_basic(mcp_server: Server, mcp_pane: Pane) -> None:
    """search_panes smoke test with a unique marker."""
    mcp_pane.send_keys("echo SMOKE_TEST_MARKER_abc123", enter=True)
    retry_until(
        lambda: "SMOKE_TEST_MARKER_abc123" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = search_panes(
        pattern="SMOKE_TEST_MARKER_abc123",
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, list)
    assert len(result) >= 1
    assert any(r.pane_id == mcp_pane.pane_id for r in result)


def test_search_panes_returns_pane_content_match_model(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """search_panes returns PaneContentMatch models."""
    mcp_pane.send_keys("echo MODEL_TYPE_CHECK_xyz", enter=True)
    retry_until(
        lambda: "MODEL_TYPE_CHECK_xyz" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = search_panes(
        pattern="MODEL_TYPE_CHECK_xyz",
        socket_name=mcp_server.socket_name,
    )
    assert len(result) >= 1
    for item in result:
        assert isinstance(item, PaneContentMatch)


def test_search_panes_includes_window_and_session_names(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """search_panes populates window_name and session_name."""
    mcp_pane.send_keys("echo CONTEXT_FIELDS_CHECK_789", enter=True)
    retry_until(
        lambda: "CONTEXT_FIELDS_CHECK_789" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = search_panes(
        pattern="CONTEXT_FIELDS_CHECK_789",
        socket_name=mcp_server.socket_name,
    )
    match = next((r for r in result if r.pane_id == mcp_pane.pane_id), None)
    assert match is not None
    assert match.window_name is not None
    assert match.session_name is not None
    assert match.session_name == mcp_session.session_name


def test_search_panes_invalid_regex(mcp_server: Server, mcp_session: Session) -> None:
    """search_panes raises ToolError on invalid regex when regex=True."""
    with pytest.raises(ToolError, match="Invalid regex pattern"):
        search_panes(
            pattern="[invalid",
            regex=True,
            socket_name=mcp_server.socket_name,
        )


# ---------------------------------------------------------------------------
# search_panes is_caller annotation tests
# ---------------------------------------------------------------------------


class SearchPanesCallerFixture(t.NamedTuple):
    """Test fixture for search_panes is_caller annotation."""

    test_id: str
    tmux_pane_env: str | None
    use_real_pane_id: bool
    expected_is_caller: bool | None


SEARCH_PANES_CALLER_FIXTURES: list[SearchPanesCallerFixture] = [
    SearchPanesCallerFixture(
        test_id="caller_pane_annotated",
        tmux_pane_env=None,
        use_real_pane_id=True,
        expected_is_caller=True,
    ),
    SearchPanesCallerFixture(
        test_id="outside_tmux_no_annotation",
        tmux_pane_env=None,
        use_real_pane_id=False,
        expected_is_caller=None,
    ),
]


@pytest.mark.parametrize(
    SearchPanesCallerFixture._fields,
    SEARCH_PANES_CALLER_FIXTURES,
    ids=[f.test_id for f in SEARCH_PANES_CALLER_FIXTURES],
)
def test_search_panes_is_caller(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
    test_id: str,
    tmux_pane_env: str | None,
    use_real_pane_id: bool,
    expected_is_caller: bool | None,
) -> None:
    """search_panes annotates results with is_caller based on TMUX_PANE."""
    marker = f"IS_CALLER_TEST_{test_id}_{id(mcp_pane)}"
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    if use_real_pane_id:
        monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id or "")
    elif tmux_pane_env is not None:
        monkeypatch.setenv("TMUX_PANE", tmux_pane_env)
    else:
        monkeypatch.delenv("TMUX_PANE", raising=False)

    result = search_panes(
        pattern=marker,
        socket_name=mcp_server.socket_name,
    )
    match = next((r for r in result if r.pane_id == mcp_pane.pane_id), None)
    assert match is not None
    assert match.is_caller is expected_is_caller


# ---------------------------------------------------------------------------
# wait_for_text tests
# ---------------------------------------------------------------------------


class WaitForTextFixture(t.NamedTuple):
    """Test fixture for wait_for_text."""

    test_id: str
    command: str | None
    pattern: str
    timeout: float
    expected_found: bool


WAIT_FOR_TEXT_FIXTURES: list[WaitForTextFixture] = [
    WaitForTextFixture(
        test_id="text_found",
        command="echo WAIT_MARKER_abc123",
        pattern="WAIT_MARKER_abc123",
        timeout=2.0,
        expected_found=True,
    ),
    WaitForTextFixture(
        test_id="timeout_not_found",
        command=None,
        pattern="NEVER_EXISTS_xyz999",
        timeout=0.3,
        expected_found=False,
    ),
]


@pytest.mark.parametrize(
    WaitForTextFixture._fields,
    WAIT_FOR_TEXT_FIXTURES,
    ids=[f.test_id for f in WAIT_FOR_TEXT_FIXTURES],
)
def test_wait_for_text(
    mcp_server: Server,
    mcp_pane: Pane,
    test_id: str,
    command: str | None,
    pattern: str,
    timeout: float,
    expected_found: bool,
) -> None:
    """wait_for_text polls pane content for a pattern."""
    if command is not None:
        mcp_pane.send_keys(command, enter=True)

    result = wait_for_text(
        pattern=pattern,
        pane_id=mcp_pane.pane_id,
        timeout=timeout,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, WaitForTextResult)
    assert result.found is expected_found
    assert result.timed_out is (not expected_found)
    assert result.pane_id == mcp_pane.pane_id
    assert result.elapsed_seconds >= 0

    if expected_found:
        assert len(result.matched_lines) >= 1


def test_wait_for_text_invalid_regex(mcp_server: Server, mcp_pane: Pane) -> None:
    """wait_for_text raises ToolError on invalid regex when regex=True."""
    with pytest.raises(ToolError, match="Invalid regex pattern"):
        wait_for_text(
            pattern="[invalid",
            regex=True,
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )


# ---------------------------------------------------------------------------
# snapshot_pane tests
# ---------------------------------------------------------------------------


def test_snapshot_pane(mcp_server: Server, mcp_pane: Pane) -> None:
    """snapshot_pane returns rich metadata alongside content."""
    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, PaneSnapshot)
    assert result.pane_id == mcp_pane.pane_id
    assert isinstance(result.content, str)
    assert result.cursor_x >= 0
    assert result.cursor_y >= 0
    assert result.pane_width > 0
    assert result.pane_height > 0
    assert result.pane_in_mode is False
    assert result.pane_mode is None
    assert result.history_size >= 0


def test_snapshot_pane_cursor_moves(mcp_server: Server, mcp_pane: Pane) -> None:
    """snapshot_pane reflects cursor position changes."""
    mcp_pane.send_keys("echo hello_snapshot", enter=True)
    retry_until(
        lambda: "hello_snapshot" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "hello_snapshot" in result.content
    assert result.pane_current_command is not None


def test_snapshot_pane_pads_short_display_message_output(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """snapshot_pane survives a truncated display-message result.

    Older tmux versions may drop unknown format variables (e.g.
    `#{pane_mode}`), producing fewer delimited fields than expected.
    Defensive padding must guarantee 11 fields so index access in the
    parser never raises IndexError.
    """
    # Capture the real cmd so non-display-message calls still work.
    real_cmd = mcp_pane.__class__.cmd

    def fake_cmd(self, cmd_name, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = real_cmd(self, cmd_name, *args, **kwargs)
        if cmd_name == "display-message":
            # Return only the first 2 fields (cursor_x, cursor_y) —
            # simulate an old tmux that dropped several unknown format
            # variables. Without defensive padding, parts[2..10] would
            # IndexError.
            parts = result.stdout[0].split("␞") if result.stdout else [""]
            result.stdout = ["␞".join(parts[:2])]
        return result

    monkeypatch.setattr(mcp_pane.__class__, "cmd", fake_cmd)

    # Must not raise IndexError; missing fields default to zero/None.
    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, PaneSnapshot)
    assert result.pane_width == 0
    assert result.pane_height == 0
    assert result.history_size == 0
    assert result.title is None
    assert result.pane_current_command is None
    assert result.pane_current_path is None


# ---------------------------------------------------------------------------
# wait_for_content_change tests
# ---------------------------------------------------------------------------


def test_wait_for_content_change_detects_change(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """wait_for_content_change detects screen changes."""
    import threading

    # Send a command after a brief delay to trigger a change
    def _send_later() -> None:
        import time

        time.sleep(0.2)
        mcp_pane.send_keys("echo CHANGE_DETECTED_xyz", enter=True)

    thread = threading.Thread(target=_send_later)
    thread.start()

    result = wait_for_content_change(
        pane_id=mcp_pane.pane_id,
        timeout=3.0,
        socket_name=mcp_server.socket_name,
    )
    thread.join()
    assert isinstance(result, ContentChangeResult)
    assert result.changed is True
    assert result.timed_out is False
    assert result.elapsed_seconds > 0


def test_wait_for_content_change_timeout(mcp_server: Server, mcp_pane: Pane) -> None:
    """wait_for_content_change times out when no change occurs.

    Uses an active-polling settle loop instead of a fixed sleep: we wait
    until two consecutive ``capture_pane`` reads return the same content
    before starting the no-change assertion. On slow or loaded CI
    machines the shell prompt can take well over 500 ms to fully render
    (cursor blink, zsh right-prompt, git status async hooks) and would
    otherwise be observed as pane-content change during the test window,
    failing ``timed_out=True`` spuriously under ``--reruns=0``.
    """
    import time

    #: Number of consecutive matching captures required to call the pane
    #: "settled". One match is unreliable under zsh async hooks (vcs_info,
    #: git prompt, right-prompt) that render after an initial quiet
    #: window. Three requires ~300 ms of continuous quiescence which is
    #: enough to outwait those hooks on loaded CI.
    settle_streak_required = 3
    settle_poll_interval = 0.1

    previous = mcp_pane.capture_pane()
    streak = 0
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        time.sleep(settle_poll_interval)
        current = mcp_pane.capture_pane()
        if current == previous:
            streak += 1
            if streak >= settle_streak_required:
                break
        else:
            streak = 0
            previous = current
    else:
        pytest.fail("pane content did not settle within 5s")

    result = wait_for_content_change(
        pane_id=mcp_pane.pane_id,
        timeout=0.5,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, ContentChangeResult)
    assert result.changed is False
    assert result.timed_out is True


# ---------------------------------------------------------------------------
# select_pane tests
# ---------------------------------------------------------------------------


def test_select_pane_by_id(mcp_server: Server, mcp_session: Session) -> None:
    """select_pane focuses a specific pane by ID."""
    window = mcp_session.active_window
    pane1 = window.active_pane
    assert pane1 is not None
    window.split()

    # Select the first pane
    result = select_pane(
        pane_id=pane1.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane1.pane_id


def test_select_pane_directional(mcp_server: Server, mcp_session: Session) -> None:
    """select_pane navigates using direction."""
    window = mcp_session.active_window
    pane1 = window.active_pane
    assert pane1 is not None
    pane2 = window.split()  # creates pane below; pane1 stays active

    # pane1 is active, select "down" should go to pane2
    result = select_pane(
        direction="down",
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane2.pane_id


def test_select_pane_requires_target(mcp_server: Server) -> None:
    """select_pane raises ToolError when neither pane_id nor direction given."""
    with pytest.raises(ToolError, match="Provide either"):
        select_pane(socket_name=mcp_server.socket_name)


def test_select_pane_next_previous_respects_target_window(
    mcp_server: Server, mcp_session: Session
) -> None:
    """select_pane direction=next/previous must anchor to window_id.

    Regression guard: bare `-t +1` / `-t -1` pane targets resolve
    against the attached client's current window (tmux cmd-find.c),
    not against any earlier -t on the command line. Targeting a
    non-active window must use a window-scoped syntax like
    `@window_id.+` to actually affect that window. Without the fix,
    calling select_pane(direction='next', window_id=w2) when w1 is
    the client's active window shifts focus in w1 and leaves w2
    untouched.
    """
    w1 = mcp_session.active_window
    assert w1.active_pane is not None
    w1.split()
    w1.split()
    w2 = mcp_session.new_window()
    w2.split()
    w2.split()

    # Make w1 the active window again, so w2 is the NON-active target.
    w1.select()
    w1.refresh()
    w2.refresh()

    w1_before = w1.active_pane.pane_id
    assert w2.active_pane is not None
    w2_before = w2.active_pane.pane_id

    result = select_pane(
        direction="next",
        window_id=w2.window_id,
        socket_name=mcp_server.socket_name,
    )

    w1.refresh()
    w2.refresh()
    assert w2.active_pane is not None
    w2_after = w2.active_pane.pane_id
    assert w1.active_pane is not None
    w1_after = w1.active_pane.pane_id

    # Result must describe a pane in w2 (the target), not w1.
    w2_pane_ids = {p.pane_id for p in w2.panes}
    assert result.pane_id in w2_pane_ids, (
        f"select_pane returned {result.pane_id} which is not in target "
        f"window {w2.window_id}'s panes {w2_pane_ids}"
    )
    # w2's active pane must have actually changed.
    assert w2_after != w2_before, "target window w2's active pane did not change"
    # w1's active pane must NOT have changed — the wrong-window bug.
    assert w1_after == w1_before, (
        f"select_pane targeting w2 shifted focus in w1 "
        f"({w1_before} -> {w1_after}) — anchor missing"
    )


# ---------------------------------------------------------------------------
# swap_pane tests
# ---------------------------------------------------------------------------


def test_swap_pane(mcp_server: Server, mcp_session: Session) -> None:
    """swap_pane exchanges two pane positions."""
    window = mcp_session.active_window
    pane1 = window.active_pane
    assert pane1 is not None
    pane2 = window.split()

    assert pane1.pane_id is not None
    assert pane2.pane_id is not None

    result = swap_pane(
        source_pane_id=pane1.pane_id,
        target_pane_id=pane2.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane1.pane_id


# ---------------------------------------------------------------------------
# pipe_pane tests
# ---------------------------------------------------------------------------


def test_pipe_pane_start_stop(
    mcp_server: Server, mcp_pane: Pane, tmp_path: t.Any
) -> None:
    """pipe_pane starts writes after start and halts writes after stop."""
    log_file = tmp_path / "pane_output.log"

    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=str(log_file),
        socket_name=mcp_server.socket_name,
    )
    assert "piping" in result.lower()

    mcp_pane.send_keys("echo START_MARKER_42", enter=True)
    retry_until(
        lambda: log_file.exists() and "START_MARKER_42" in log_file.read_text(),
        2,
        raises=True,
    )

    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=None,
        socket_name=mcp_server.socket_name,
    )
    assert "stopped" in result.lower()

    size_after_stop = log_file.stat().st_size
    mcp_pane.send_keys("echo POST_STOP_MARKER_99", enter=True)
    # Poll briefly — if stop worked the file must not grow.
    with pytest.raises(libtmux_exc.WaitTimeout):
        retry_until(
            lambda: log_file.stat().st_size > size_after_stop,
            1,
            raises=True,
        )
    assert "POST_STOP_MARKER_99" not in log_file.read_text()


def test_pipe_pane_quotes_path_with_spaces(
    mcp_server: Server, mcp_pane: Pane, tmp_path: t.Any
) -> None:
    """pipe_pane survives an output_path containing spaces.

    Without shell-quoting the path, tmux runs `cat >> /tmp/has space.log`
    which the shell splits into two arguments — the redirect silently
    lands on `/tmp/has` and `space.log` becomes a literal cat argument.
    """
    log_file = tmp_path / "has space.log"
    marker = "PIPE_PANE_MARKER_42"

    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=str(log_file),
        socket_name=mcp_server.socket_name,
    )
    assert "piping" in result.lower()

    try:
        mcp_pane.send_keys(f"echo {marker}", enter=True)
        retry_until(
            lambda: log_file.exists() and marker in log_file.read_text(),
            2,
            raises=True,
        )
    finally:
        pipe_pane(
            pane_id=mcp_pane.pane_id,
            output_path=None,
            socket_name=mcp_server.socket_name,
        )


def test_pipe_pane_rejects_empty_path(mcp_server: Server, mcp_pane: Pane) -> None:
    """pipe_pane raises ToolError when output_path is empty or whitespace."""
    for bad in ("", "   ", "\t"):
        with pytest.raises(ToolError, match="non-empty"):
            pipe_pane(
                pane_id=mcp_pane.pane_id,
                output_path=bad,
                socket_name=mcp_server.socket_name,
            )


# ---------------------------------------------------------------------------
# display_message tests
# ---------------------------------------------------------------------------


def test_display_message(mcp_server: Server, mcp_pane: Pane) -> None:
    """display_message expands tmux format strings."""
    result = display_message(
        format_string="#{pane_width}x#{pane_height}",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "x" in result
    parts = result.split("x")
    assert len(parts) == 2
    assert parts[0].isdigit()
    assert parts[1].isdigit()


def test_display_message_zoomed_flag(mcp_server: Server, mcp_session: Session) -> None:
    """display_message queries arbitrary tmux variables."""
    window = mcp_session.active_window
    pane = window.active_pane
    assert pane is not None
    result = display_message(
        format_string="#{window_zoomed_flag}",
        pane_id=pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result in ("0", "1")


# ---------------------------------------------------------------------------
# enter_copy_mode / exit_copy_mode tests
# ---------------------------------------------------------------------------


def test_enter_and_exit_copy_mode(mcp_server: Server, mcp_pane: Pane) -> None:
    """enter_copy_mode enters copy mode, exit_copy_mode leaves it."""
    enter_result = enter_copy_mode(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert enter_result.pane_id == mcp_pane.pane_id

    # Verify pane is in copy mode via snapshot
    snap = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert snap.pane_in_mode is True

    exit_result = exit_copy_mode(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert exit_result.pane_id == mcp_pane.pane_id


def test_enter_copy_mode_with_scroll(mcp_server: Server, mcp_pane: Pane) -> None:
    """enter_copy_mode can scroll up immediately."""
    # Generate some scrollback history
    for i in range(20):
        mcp_pane.send_keys(f"echo scrollback_line_{i}", enter=True)
    retry_until(
        lambda: "scrollback_line_19" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    enter_result = enter_copy_mode(
        pane_id=mcp_pane.pane_id,
        scroll_up=5,
        socket_name=mcp_server.socket_name,
    )
    assert enter_result.pane_id == mcp_pane.pane_id

    # Clean up: exit copy mode
    exit_copy_mode(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )


# ---------------------------------------------------------------------------
# paste_text tests
# ---------------------------------------------------------------------------


def test_paste_text(mcp_server: Server, mcp_pane: Pane) -> None:
    """paste_text pastes text into a pane via tmux buffer.

    Uses bracket=False and a trailing newline so the shell actually
    executes the echo command. Previous versions of this test
    relied on the default bracket=True, which is fragile on CI:
    bash readline needs a prompt cycle to latch bracketed-paste
    mode, and if the paste arrives before that the escape sequences
    get consumed as unrecognized input and the marker never reaches
    the visible pane buffer. bracket=False sends raw bytes and the
    trailing newline forces execution, exercising the full
    paste->execute->output round-trip.
    """
    result = paste_text(
        text="echo PASTE_TEST_marker_xyz\n",
        pane_id=mcp_pane.pane_id,
        bracket=False,
        socket_name=mcp_server.socket_name,
    )
    assert "pasted" in result.lower()

    # Verify the echoed marker reaches the pane. 10 seconds is
    # generous on local machines (<1s) but tolerates slow CI
    # runners where bash cold-start can exceed the default budget.
    retry_until(
        lambda: "PASTE_TEST_marker_xyz" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )


def test_paste_text_does_not_leak_named_buffer(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """paste_text must not leave its mcp_paste_* buffer behind.

    Regression guard for the pre-fix behavior: the earlier
    implementation used tmux's default unnamed buffer AND relied on
    `paste-buffer -d` to clean up. If paste-buffer failed mid-flight
    the buffer leaked. The fix generates a unique `mcp_paste_<uuid>`
    named buffer per call and adds a best-effort `delete-buffer -b`
    in `finally` so the server is left in a clean state on both
    success and failure paths.

    The check is portable across every tmux version the CI matrix
    tests (3.2a through master): list-buffers with a format string
    returns buffer names without any version-specific behavior.
    """
    paste_text(
        text="echo BUFFER_ISOLATION_test",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )

    listing = mcp_server.cmd("list-buffers", "-F", "#{buffer_name}")
    buffer_names = "\n".join(listing.stdout or [])
    assert "mcp_paste_" not in buffer_names, (
        f"paste_text leaked a named buffer: {buffer_names!r}"
    )


# ---------------------------------------------------------------------------
# Registration-time annotation verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected_open_world"),
    [
        # Shell-driving tools: the command the caller sends can reach
        # arbitrary external state, so the interaction is open-world.
        ("send_keys", True),
        ("paste_text", True),
        ("pipe_pane", True),
        # Create-style tools: allocate tmux objects only. Not open-world
        # even though they share the old ANNOTATIONS_CREATE preset.
        ("swap_pane", False),
        ("enter_copy_mode", False),
    ],
)
def test_pane_tool_open_world_hint_registration(
    tool_name: str, expected_open_world: bool
) -> None:
    """Pane tools advertise ``openWorldHint`` matching their real semantics.

    Regression guard for the shared-preset trap: the old
    ``ANNOTATIONS_CREATE`` preset was applied to both shell-driving and
    non-shell-driving tools, so every caller saw ``openWorldHint=False``.
    A new ``ANNOTATIONS_SHELL`` preset now carries ``openWorldHint=True``
    for the three shell-driving tools only, leaving the other
    ``ANNOTATIONS_CREATE`` users unchanged.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import pane_tools

    mcp = FastMCP(name="test-pane-annotations")
    pane_tools.register(mcp)

    tool = asyncio.run(mcp.get_tool(tool_name))
    assert tool is not None, f"{tool_name} should be registered"
    assert tool.annotations is not None, (
        f"{tool_name} registration should carry annotations"
    )
    assert tool.annotations.openWorldHint is expected_open_world
