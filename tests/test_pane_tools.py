"""Tests for libtmux MCP pane tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError
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
    """wait_for_content_change times out when no change occurs."""
    # Wait for the shell prompt to settle before testing for "no change"
    import time

    time.sleep(0.5)

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
    """pipe_pane starts and stops piping output to a file."""
    log_file = str(tmp_path / "pane_output.log")

    # Start piping
    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=log_file,
        socket_name=mcp_server.socket_name,
    )
    assert "piping" in result.lower()

    # Stop piping
    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=None,
        socket_name=mcp_server.socket_name,
    )
    assert "stopped" in result.lower()


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
    """paste_text pastes text into a pane via tmux buffer."""
    result = paste_text(
        text="echo PASTE_TEST_marker_xyz",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "pasted" in result.lower()

    # Verify the text appeared in the pane
    retry_until(
        lambda: "PASTE_TEST_marker_xyz" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )
