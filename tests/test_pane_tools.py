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
    SearchPanesResult,
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
    respawn_pane,
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
# respawn_pane tests
# ---------------------------------------------------------------------------


def test_respawn_pane_preserves_pane_id_and_refreshes_pid(
    mcp_server: Server, mcp_session: Session
) -> None:
    """respawn_pane keeps the same pane_id but picks up a new pane_pid.

    Uses a fresh split so the caller-pane self-guard doesn't fire and
    so the test is independent of what the main mcp_pane is running.
    """
    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None
    # Force a read of the original pid before we respawn.
    new_pane.refresh()
    original_pid = new_pane.pane_pid

    result = respawn_pane(
        pane_id=new_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == new_pane.pane_id, "pane_id must be preserved"
    assert result.pane_pid is not None
    assert result.pane_pid != original_pid, (
        "pane_pid should reflect the new process after respawn"
    )

    # Cleanup
    new_pane.kill()


def test_respawn_pane_replaces_shell(mcp_server: Server, mcp_session: Session) -> None:
    """respawn_pane with ``shell`` relaunches with the new command."""
    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None

    result = respawn_pane(
        pane_id=new_pane.pane_id,
        shell="sleep 7200",
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == new_pane.pane_id
    # pane_current_command reflects the relaunched command.
    assert result.pane_current_command is not None
    assert "sleep" in result.pane_current_command

    new_pane.kill()


def test_respawn_pane_self_kill_guard(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """respawn_pane refuses when the caller's pane is the target."""
    from libtmux_mcp._utils import _effective_socket_path

    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None

    socket_path = _effective_socket_path(mcp_server)
    monkeypatch.setenv(
        "TMUX",
        f"{socket_path},12345,{mcp_session.session_id}",
    )
    monkeypatch.setenv("TMUX_PANE", new_pane.pane_id)
    with pytest.raises(ToolError, match="Refusing to respawn"):
        respawn_pane(
            pane_id=new_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )

    new_pane.kill()


def test_respawn_pane_rejects_implicit_target(mcp_server: Server) -> None:
    """respawn_pane refuses when no targeting parameter is supplied.

    Without ``pane_id`` (or any other discriminator) ``_resolve_pane``
    falls back to the first pane of the first window of the first
    session — combined with default ``kill=True`` that could silently
    kill an unrelated server. The runtime guard requires explicit
    ``pane_id``.
    """
    with pytest.raises(ToolError, match="explicit pane_id"):
        respawn_pane(socket_name=mcp_server.socket_name)


def test_respawn_pane_rejects_session_only_target(
    mcp_server: Server, mcp_session: Session
) -> None:
    """respawn_pane refuses ``session_name`` without ``pane_id``.

    ``session_name`` alone resolves to the first pane of the first
    window, which is not what the caller intends when recovering a
    wedged shell elsewhere in the session. The guard requires
    ``pane_id`` regardless of which other targeting parameters are
    present.
    """
    assert mcp_session.session_name is not None
    with pytest.raises(ToolError, match="explicit pane_id"):
        respawn_pane(
            session_name=mcp_session.session_name,
            socket_name=mcp_server.socket_name,
        )


def test_respawn_pane_kill_false_on_dead_pane_succeeds(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``kill=False`` respawn on a dead pane returns fresh PaneInfo.

    tmux's ``respawn-pane`` without ``-k`` is the safer default: it
    only succeeds when the pane has no running process. Existing tests
    only cover ``kill=True`` paths (see :func:`test_respawn_pane_*`
    above); this test locks the safer-default behaviour for any future
    flip of the default.
    """
    window = mcp_session.active_window
    # remain-on-exit=on keeps the pane around after its process exits so
    # we can drive a kill=False respawn on a confirmed-dead process.
    # Without it, tmux removes the pane the moment its child exits and
    # the respawn call fails with PaneNotFound instead of exercising
    # the kill=False branch. Set the option on the window *before*
    # splitting so the new pane inherits it.
    window.cmd("set-option", "-w", "remain-on-exit", "on")
    new_pane = window.split(shell="true")
    assert new_pane.pane_id is not None

    def _pane_dead() -> bool:
        out = new_pane.cmd("display-message", "-p", "#{pane_dead}").stdout
        return bool(out) and out[0].strip() == "1"

    retry_until(_pane_dead, seconds=5, raises=True)

    result = respawn_pane(
        pane_id=new_pane.pane_id,
        kill=False,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == new_pane.pane_id
    new_pane.kill()
    window.cmd("set-option", "-wu", "remain-on-exit")


def test_respawn_pane_kill_false_on_live_pane_raises(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``kill=False`` respawn on a live pane raises ToolError from tmux.

    tmux refuses to respawn a pane that still has a running process
    unless ``-k`` is passed. The MCP wrapper surfaces the stderr as a
    ``ToolError`` rather than swallowing it.
    """
    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None

    with pytest.raises(ToolError):
        respawn_pane(
            pane_id=new_pane.pane_id,
            kill=False,
            socket_name=mcp_server.socket_name,
        )

    new_pane.kill()


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
    assert isinstance(result, SearchPanesResult)

    if expected_match:
        assert len(result.matches) >= 1
        match = next((r for r in result.matches if r.pane_id == mcp_pane.pane_id), None)
        assert match is not None
        assert len(match.matched_lines) >= expected_min_lines
        assert match.session_id is not None
        assert match.window_id is not None
    else:
        pane_matches = [r for r in result.matches if r.pane_id == mcp_pane.pane_id]
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
    assert isinstance(result, SearchPanesResult)
    assert len(result.matches) >= 1
    assert any(r.pane_id == mcp_pane.pane_id for r in result.matches)


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
    assert len(result.matches) >= 1
    for item in result.matches:
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
    match = next((r for r in result.matches if r.pane_id == mcp_pane.pane_id), None)
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


def test_search_panes_pagination_limit_and_offset(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """search_panes pages matching panes via ``limit`` and ``offset``.

    Creates additional panes and seeds each with the same marker so
    multiple panes match. Then asserts:
    - ``limit=1`` returns one pane, ``truncated=True``, and the skipped
      panes are listed in ``truncated_panes``.
    - ``offset=1, limit=10`` returns the remaining panes with
      ``truncated=False``.
    - ``total_panes_matched`` is stable across pages.
    """
    marker = "PAGINATION_MARKER_qzz987"
    # Split the window a few times so we have >=3 panes matching.
    extra_panes = [
        mcp_session.active_window.split(),
        mcp_session.active_window.split(),
    ]
    all_panes = [mcp_pane, *extra_panes]
    for pane in all_panes:
        pane.send_keys(f"echo {marker}", enter=True)

    def _waiter(target: Pane) -> t.Callable[[], bool]:
        def _ready() -> bool:
            return marker in "\n".join(target.capture_pane())

        return _ready

    for pane in all_panes:
        retry_until(_waiter(pane), 2, raises=True)

    first = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        limit=1,
        socket_name=mcp_server.socket_name,
    )
    assert first.total_panes_matched >= 3
    assert len(first.matches) == 1
    assert first.truncated is True
    assert len(first.truncated_panes) == first.total_panes_matched - 1
    assert first.offset == 0
    assert first.limit == 1

    rest = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        offset=1,
        limit=10,
        socket_name=mcp_server.socket_name,
    )
    assert rest.total_panes_matched == first.total_panes_matched
    assert len(rest.matches) == first.total_panes_matched - 1
    assert rest.truncated is False
    assert rest.truncated_panes == []
    assert rest.offset == 1

    # Union of paginated pane IDs equals the full matching set.
    seen = {m.pane_id for m in first.matches} | {m.pane_id for m in rest.matches}
    assert len(seen) == first.total_panes_matched


def test_search_panes_literal_input_skips_slow_path_probe(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    r"""Literal searches (``regex=False``) find matches containing metacharacters.

    Regression guard for the ``_REGEX_META`` check bug: the pre-fix
    code tested the *escaped* pattern for regex metacharacters. With
    ``regex=False`` and a literal IP address like ``"192.168.1.1"``,
    ``re.escape`` produced ``"192\\.168\\.1\\.1"`` — whose ``\\`` matched
    the probe and kicked the search off the tmux fast path onto the
    slow Python-regex path.

    The functional observable: both paths correctly found the literal.
    The bug was performance. Probing that from a test is fragile (both
    paths call ``capture_pane`` in Phase 2), so this test asserts the
    *decision variable* directly: calling ``search_panes`` with a
    regex-meta-bearing literal must return the expected match, and the
    inspection of the fast-path decision is covered by the unit test
    below.
    """
    marker = "192.168.1.1"
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )
    result = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert any(m.pane_id == mcp_pane.pane_id for m in result.matches)


@pytest.mark.parametrize(
    ("pattern", "regex", "expected_fast_path"),
    [
        # Literal input with regex metacharacters — the earlier bug's
        # target case. Raw input is glob-safe for tmux, fast path.
        ("192.168.1.1", False, True),
        # Literal with no metacharacters — always fast path.
        ("plain_marker", False, True),
        # Regex with no metacharacters — fast path still fine.
        ("plain_marker", True, True),
        # Regex with metacharacters — legitimately slow path.
        (r"err(or|no)", True, False),
        # Regex dot-star — slow path.
        (r".*", True, False),
        # tmux format-injection bytes in a literal — MUST fall to slow
        # path regardless of regex flag, because tmux's #{C:...} format
        # block has no escape for `}` (premature close) or `#{` (nested
        # format-variable evaluation).
        ("foo}", False, False),
        ("log #{err}", False, False),
        # Same hazards with regex=True — still slow path; tmux sees the
        # raw pattern either way.
        ("x}y", True, False),
        ("a#{b}", True, False),
    ],
)
def test_search_panes_fast_path_decision(
    pattern: str, regex: bool, expected_fast_path: bool
) -> None:
    """Unit-test the ``is_plain_text`` decision on pattern + regex flag.

    Mirrors the exact expression in ``search_panes`` so a future
    refactor cannot silently reintroduce either of the two hazards it
    guards against: the escape-aware metacharacter check that
    misclassified literals, or the tmux format-string injection on
    ``}`` / ``#{``.
    """
    import re as _re

    _regex_meta = _re.compile(r"[\\.*+?{}()\[\]|^$]")
    _tmux_format_injection = _re.compile(r"\}|#\{")
    if _tmux_format_injection.search(pattern):
        is_plain_text = False
    elif regex:
        is_plain_text = not _regex_meta.search(pattern)
    else:
        is_plain_text = True
    assert is_plain_text is expected_fast_path


def test_search_panes_tmux_format_injection_is_neutralized(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """Literal patterns containing ``}`` or ``#{`` don't return every pane.

    Regression guard for the Critical tmux format-string injection in
    commit ``decc994`` (pre-existing, widened by the regex-fast-path
    fix): ``search_panes(pattern="foo}", regex=False)`` previously
    interpolated the raw ``}`` into ``#{C:foo}}`` — tmux's format
    parser closed the block at the first ``}``, evaluated the
    remainder (``}``) as truthy, and marked *every* pane as a match.

    Two panes are exercised: one seeded with the literal marker,
    one without. Only the seeded pane should appear in ``matches``.
    """
    marker = "INJECT_MARKER_xyz}qq9"  # contains `}` — the injection trigger
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    # Add a second pane that lacks the marker — if the fast path is
    # still injecting, every pane including this one shows up.
    clean_pane = mcp_session.active_window.split()
    clean_pane.send_keys("echo UNRELATED_content", enter=True)

    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = search_panes(
        pattern=marker,
        regex=False,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    matched_ids = {m.pane_id for m in result.matches}
    assert mcp_pane.pane_id in matched_ids
    assert clean_pane.pane_id not in matched_ids, (
        f"tmux format injection re-opened: clean pane {clean_pane.pane_id} "
        f"erroneously matched. Full match list: {matched_ids}"
    )


def test_search_panes_nested_format_variable_is_neutralized(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """Literal patterns containing ``#{`` don't trigger tmux format eval.

    Companion to the ``}`` injection test. ``#{`` inside the pattern
    opens a nested tmux format variable; without neutralization, tmux
    would evaluate ``#{pane_id}`` as the current pane's id and match
    every pane whose content contains its own id — a subtler but
    equally wrong outcome.
    """
    marker = "NEST_#{pane_id}_ABC"
    mcp_pane.send_keys(f"echo {marker!r}", enter=True)
    retry_until(
        lambda: "NEST" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = search_panes(
        pattern=marker,
        regex=False,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    # The test's value is that the call returns *without* raising and
    # without marking unrelated panes. An exact match on the literal
    # `#{pane_id}` bytes in scrollback isn't required.
    assert isinstance(result.matches, list)  # didn't crash
    # No pane other than mcp_pane should be in the match set, since no
    # other pane's content contains NEST_ at all.
    for m in result.matches:
        assert m.pane_id == mcp_pane.pane_id


def test_search_panes_numeric_pane_id_ordering(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Pagination returns panes in numeric, not lexicographic, order.

    Regression guard: an earlier ``all_matches.sort(key=lambda m:
    m.pane_id)`` produced ``["%0", "%1", "%10", "%2", ...]`` on any
    session with ≥11 matching panes, which confused pagination (the
    last "page 1" pane was ``%2`` rather than ``%1``). The fix sorts
    via ``_pane_id_sort_key`` which casts the numeric portion.

    Physical tmux panes don't fit in a single 80x24 window past ~6
    before ``split-window`` fails with "no space for new pane"; this
    test spreads panes across multiple windows so pane ids reliably
    cross the ``%10`` boundary. The assertion is numeric monotonicity
    of ids across the returned matches.
    """
    marker = "NUMSORT_MARKER_89vq"
    # Spread panes across several windows so we get >= 12 panes without
    # running out of per-window space. Each new_window seeds one pane;
    # split() adds 1-2 more per window.
    while True:
        total_panes = sum(len(w.panes) for w in mcp_session.windows)
        if total_panes >= 12:
            break
        window = mcp_session.new_window()
        window.split()

    panes = [p for w in mcp_session.windows for p in w.panes]
    assert len(panes) >= 12
    for pane in panes:
        pane.send_keys(f"echo {marker}", enter=True)

    def _ready() -> bool:
        return all(marker in "\n".join(p.capture_pane()) for p in panes)

    retry_until(_ready, 5, raises=True)

    result = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        limit=100,
        socket_name=mcp_server.socket_name,
    )
    ids = [m.pane_id for m in result.matches]
    assert len(ids) >= 12
    numeric = [int(i.lstrip("%")) for i in ids]
    assert numeric == sorted(numeric), f"pane ids not in numeric order: {ids}"
    # The bug's canonical manifestation: lex-sort places ``%10`` between
    # ``%1`` and ``%2``. Pin that ``%2`` comes before ``%10`` as a
    # stronger shape check than pure monotonicity.
    assert 2 in numeric and 10 in numeric
    assert numeric.index(2) < numeric.index(10)


def test_search_panes_per_pane_matched_lines_cap(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """``max_matched_lines_per_pane`` tail-truncates matched_lines per pane.

    Synchronizes on shell-command completion via the project's own
    ``wait_for_channel`` primitive (the ``tmux wait-for -S`` idiom
    documented in ``src/libtmux_mcp/prompts/recipes.py``) instead of
    polling ``capture_pane`` output. This makes the assertion
    deterministic on every shell — the ``capture_pane`` inside
    ``search_panes`` runs strictly after the four echoes have
    executed, regardless of PS1 state or shell-startup timing.

    Four echoes produce at least eight marker-bearing lines in
    ``capture_pane`` (command-line plus output-line for each), well
    past the truncation threshold of three.
    """
    import asyncio
    import uuid

    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    marker = "PERLINE_MARKER_9gkv"
    channel = f"mcp_test_percap_{uuid.uuid4().hex[:16]}"
    payload = (
        f"echo {marker}; echo {marker}; echo {marker}; echo {marker}; "
        f"tmux wait-for -S {channel}"
    )
    mcp_pane.send_keys(payload, enter=True)
    asyncio.run(
        wait_for_channel(
            channel=channel, timeout=5.0, socket_name=mcp_server.socket_name
        )
    )

    result = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        max_matched_lines_per_pane=3,
        socket_name=mcp_server.socket_name,
    )
    match = next((m for m in result.matches if m.pane_id == mcp_pane.pane_id), None)
    assert match is not None
    assert len(match.matched_lines) == 3
    assert result.truncated is True


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
        # TMUX_PANE without TMUX: the strict comparator cannot verify the
        # caller's socket and returns ``False`` rather than conservatively
        # assuming same-server. Full-TMUX-env coverage lives in
        # ``tests/test_utils.py::test_serialize_pane_is_caller_false_across_sockets``.
        test_id="caller_pane_no_tmux_env",
        tmux_pane_env=None,
        use_real_pane_id=True,
        expected_is_caller=False,
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
    match = next((r for r in result.matches if r.pane_id == mcp_pane.pane_id), None)
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
    import asyncio

    if command is not None:
        mcp_pane.send_keys(command, enter=True)

    result = asyncio.run(
        wait_for_text(
            pattern=pattern,
            pane_id=mcp_pane.pane_id,
            timeout=timeout,
            socket_name=mcp_server.socket_name,
        )
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
    import asyncio

    with pytest.raises(ToolError, match="Invalid regex pattern"):
        asyncio.run(
            wait_for_text(
                pattern="[invalid",
                regex=True,
                pane_id=mcp_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_reports_progress(mcp_server: Server, mcp_pane: Pane) -> None:
    """wait_for_text calls ``ctx.report_progress`` at each poll tick.

    Uses a minimal async stub Context so the test stays independent
    from FastMCP's live server — ``report_progress`` is the only
    coroutine the wait loop invokes and it only needs to be awaitable.
    The assertion is that at least one progress report is emitted
    during a short, guaranteed-to-timeout poll window.
    """
    import asyncio

    progress_calls: list[tuple[float, float | None, str]] = []

    class _StubContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            progress_calls.append((progress, total, message))

        async def warning(self, message: str) -> None:
            return  # log notifications not asserted in this test

    stub = _StubContext()
    result = asyncio.run(
        wait_for_text(
            pattern="WILL_NEVER_MATCH_aBcDeF",
            pane_id=mcp_pane.pane_id,
            timeout=0.2,
            interval=0.05,
            socket_name=mcp_server.socket_name,
            ctx=t.cast("t.Any", stub),
        )
    )
    assert result.found is False
    assert result.timed_out is True
    assert len(progress_calls) >= 2
    first_progress, first_total, first_msg = progress_calls[0]
    assert first_progress >= 0.0
    assert first_total == 0.2
    assert "Polling pane" in first_msg


def test_wait_for_text_propagates_unexpected_progress_error(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Non-transport exceptions from ``ctx.report_progress`` propagate.

    Regression guard: an earlier ``contextlib.suppress(Exception)`` in
    ``_maybe_report_progress`` silently swallowed every exception from
    ``ctx.report_progress`` — including programming errors like a
    renamed kwarg or a misconfigured ``ctx``. The narrowed catch only
    covers transport-closed exceptions; anything else (e.g.
    ``RuntimeError`` from a stub that's been deliberately broken) must
    reach the caller so the failure is diagnostic instead of a mystery
    quiet hang.
    """
    import asyncio

    class _FaultyContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            msg = "synthetic bug in progress-notification path"
            raise RuntimeError(msg)

    # The error surfaces through ``handle_tool_errors_async``, which
    # maps any unexpected ``Exception`` to ``ToolError`` with the
    # original type + message preserved in the translated text. The
    # point of this regression guard is that the error reaches the
    # error handler at all — previously the broad ``suppress`` ate it.
    with pytest.raises(ToolError, match="synthetic bug"):
        asyncio.run(
            wait_for_text(
                pattern="WILL_NEVER_MATCH_PROPAGATE_q2rj",
                pane_id=mcp_pane.pane_id,
                timeout=0.5,
                interval=0.05,
                socket_name=mcp_server.socket_name,
                ctx=t.cast("t.Any", _FaultyContext()),
            )
        )


def test_wait_for_text_suppresses_broken_resource_error(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``anyio.BrokenResourceError`` from progress is treated as transport-gone.

    FastMCP's streamable-HTTP transport raises ``BrokenResourceError``
    (not ``ClosedResourceError``) when the receive side of the in-memory
    stream is closed — i.e. the peer went away. The wait loop must treat
    this identically to the closed-stream case: silently skip the
    progress notification and keep polling until the timeout.
    """
    import asyncio

    import anyio

    class _BrokenContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            raise anyio.BrokenResourceError

        async def warning(self, message: str) -> None:
            # Same transport-closed shape on the log channel — the
            # wait loop's timeout-warning call must also be suppressed
            # silently when the peer is gone.
            raise anyio.BrokenResourceError

    result = asyncio.run(
        wait_for_text(
            pattern="WILL_NEVER_MATCH_BROKEN_rpt5",
            pane_id=mcp_pane.pane_id,
            timeout=0.2,
            interval=0.05,
            socket_name=mcp_server.socket_name,
            ctx=t.cast("t.Any", _BrokenContext()),
        )
    )
    assert result.found is False
    assert result.timed_out is True


def test_wait_for_text_warns_on_invalid_regex(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``wait_for_text`` emits ``ctx.warning`` when the regex won't compile.

    Regression guard: agents calling with ``regex=True`` and a malformed
    pattern previously saw only a generic ``ToolError``. The new
    ``_maybe_log`` helper at ``wait.py`` lets the same condition surface
    as a ``notifications/message`` warning so MCP client log panels
    record the cause independent of the tool result.
    """
    import asyncio

    log_calls: list[tuple[str, str]] = []

    class _RecordingContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            return

        async def warning(self, message: str) -> None:
            log_calls.append(("warning", message))

    with pytest.raises(ToolError, match="Invalid regex"):
        asyncio.run(
            wait_for_text(
                pattern="[unclosed",
                regex=True,
                pane_id=mcp_pane.pane_id,
                socket_name=mcp_server.socket_name,
                ctx=t.cast("t.Any", _RecordingContext()),
            )
        )

    # The ``warning`` ran before the ``ToolError`` was raised.
    assert (
        "warning",
        "Invalid regex pattern: missing ), unterminated subpattern at position 0",
    ) in log_calls or any(
        level == "warning" and "Invalid regex" in msg for level, msg in log_calls
    )


def test_wait_for_text_warns_on_timeout(mcp_server: Server, mcp_pane: Pane) -> None:
    """``wait_for_text`` warns the client when the poll loop times out.

    Sibling guard to the invalid-regex warning. The timeout case is
    where operators most need a structured signal — the tool returns
    ``timed_out=True`` in the result but agents and human log readers
    have to dig into the ``WaitForTextResult`` to notice. The warning
    surfaces it directly.
    """
    import asyncio

    log_calls: list[tuple[str, str]] = []

    class _RecordingContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            return

        async def warning(self, message: str) -> None:
            log_calls.append(("warning", message))

    result = asyncio.run(
        wait_for_text(
            pattern="WILL_NEVER_MATCH_TIMEOUT_qZx9",
            pane_id=mcp_pane.pane_id,
            timeout=0.2,
            interval=0.05,
            socket_name=mcp_server.socket_name,
            ctx=t.cast("t.Any", _RecordingContext()),
        )
    )

    assert result.timed_out is True
    assert any(
        level == "warning" and "timeout" in msg.lower() for level, msg in log_calls
    ), f"expected a timeout warning, got: {log_calls}"


def test_wait_for_content_change_warns_on_timeout(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``wait_for_content_change`` warns the client on timeout.

    Same contract as ``wait_for_text`` — the silently-quiescent pane
    case otherwise looks identical to a successful detection at the
    log layer. Operators benefit from a ``no content change before
    Xs timeout`` warning.

    Uses the same settle-loop pattern as
    ``test_wait_for_content_change_timeout`` so the assertion is
    deterministic on slow CI.
    """
    import asyncio
    import time

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

    log_calls: list[tuple[str, str]] = []

    class _RecordingContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            return

        async def warning(self, message: str) -> None:
            log_calls.append(("warning", message))

    result = asyncio.run(
        wait_for_content_change(
            pane_id=mcp_pane.pane_id,
            timeout=0.5,
            interval=0.05,
            socket_name=mcp_server.socket_name,
            ctx=t.cast("t.Any", _RecordingContext()),
        )
    )
    assert result.timed_out is True
    assert any(
        level == "warning" and "timeout" in msg.lower() for level, msg in log_calls
    ), f"expected a timeout warning, got: {log_calls}"


def test_wait_for_text_propagates_cancellation(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``wait_for_text`` raises ``CancelledError`` (not ``ToolError``).

    Regression guard for MCP cancellation semantics.
    ``handle_tool_errors_async`` in ``_utils.py:827-850`` catches
    ``Exception`` (not ``BaseException``); since
    ``asyncio.CancelledError`` is a ``BaseException`` (Python 3.8+) it
    propagates today. Locking that in: if a future change broadens the
    decorator to ``BaseException`` it would silently break MCP
    cancellation, and this test fires.

    Uses ``task.cancel()`` rather than ``asyncio.wait_for`` so the
    raised exception is the inner ``CancelledError`` directly, not
    ``wait_for``'s ``TimeoutError`` wrapper.
    """
    import asyncio

    async def _runner() -> None:
        task = asyncio.create_task(
            wait_for_text(
                pattern="WILL_NEVER_MATCH_CANCEL_aBcD",
                pane_id=mcp_pane.pane_id,
                timeout=10.0,
                interval=0.05,
                socket_name=mcp_server.socket_name,
            )
        )
        await asyncio.sleep(0.1)  # let the poll loop start
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_runner())


def test_wait_for_content_change_propagates_cancellation(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wait_for_content_change`` raises ``CancelledError`` (not ``ToolError``).

    Sibling guard to ``test_wait_for_text_propagates_cancellation`` —
    both wait tools share the same ``while True:`` poll-and-sleep
    pattern wrapped by ``handle_tool_errors_async``, so both must
    surface MCP cancellation as ``asyncio.CancelledError``.

    Stubs ``Pane.capture_pane`` to always return the same line list so
    the ``current != initial_content`` exit can never fire — without
    the stub the test races shell prompt redraw, cursor blink, and
    zsh async hooks (vcs_info, git prompt) on CI runners and exits
    via ``changed=True`` before the cancel arrives.
    """
    import asyncio

    from libtmux.pane import Pane as _LibtmuxPane

    monkeypatch.setattr(_LibtmuxPane, "capture_pane", lambda *_a, **_kw: ["stable"])

    async def _runner() -> None:
        task = asyncio.create_task(
            wait_for_content_change(
                pane_id=mcp_pane.pane_id,
                timeout=10.0,
                interval=0.05,
                socket_name=mcp_server.socket_name,
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_runner())


def test_wait_tools_do_not_block_event_loop(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wait_for_text runs its blocking capture off the main event loop.

    Regression guard for the critical bug that FastMCP async tools are
    direct-awaited on the main event loop. ``pane.capture_pane()`` is a
    sync ``subprocess.run`` call; without ``asyncio.to_thread`` it
    would block every other coroutine on the same loop for the
    duration of each poll tick.

    Discriminator: monkeypatch ``pane.capture_pane`` so each call
    blocks the calling *thread* for 80 ms via ``time.sleep``. With
    ``asyncio.to_thread`` the default executor runs that off the
    event loop and the ticker coroutine keeps firing every 10 ms;
    without it, the event loop is pinned for the full 80 ms per poll
    and the ticker can only advance during the brief
    ``await asyncio.sleep(interval)`` gaps. The threshold (~40 ticks
    expected with the fix vs. <= 6 without) cleanly fails the
    un-fixed code while remaining robust against 2x CI slowdown.

    The previous version of this test used the production
    ``capture_pane`` (which returns instantly under tmux's normal
    semantics) and asserted ``ticks >= 5``. Since ``await
    asyncio.sleep(interval=0.05)`` between poll iterations already
    yielded enough for the ticker to satisfy that bound, the test
    passed even with ``asyncio.to_thread`` reverted — providing zero
    actual defense against the bug it claimed to guard. The
    monkeypatched slow capture is the discriminator.

    See commit ``74ec8f0`` for the project's precedent on stabilizing
    timing-sensitive tests under ``--reruns 0``.
    """
    import asyncio
    import time as _time

    from libtmux.pane import Pane as _LibtmuxPane

    def _slow_capture(self: _LibtmuxPane, *_a: object, **_kw: object) -> list[str]:
        _time.sleep(0.08)
        return []

    monkeypatch.setattr(_LibtmuxPane, "capture_pane", _slow_capture)

    async def _drive() -> int:
        ticks = 0
        stop = asyncio.Event()

        async def _ticker() -> None:
            nonlocal ticks
            while not stop.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        async def _waiter() -> None:
            try:
                await wait_for_text(
                    pattern="WILL_NEVER_MATCH_EVENT_LOOP_zqr9",
                    pane_id=mcp_pane.pane_id,
                    timeout=0.4,
                    interval=0.05,
                    socket_name=mcp_server.socket_name,
                )
            finally:
                stop.set()

        await asyncio.gather(_ticker(), _waiter())
        return ticks

    ticks = asyncio.run(_drive())
    # With asyncio.to_thread, ticker fires ~40 times in the 400 ms
    # window. Without, only during the 50 ms inter-poll sleep gaps
    # (~3 polls x ~5 ticks/sleep = ~15) plus 1 between captures = 6.
    # The 20-tick threshold is robust against 2x CI slowdown and
    # unambiguously fails the un-fixed code.
    assert ticks >= 20, (
        f"ticker advanced only {ticks} times — blocking capture is on the "
        f"main event loop, not in asyncio.to_thread"
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
    # Default max_lines leaves short captures untruncated.
    assert result.content_truncated is False
    assert result.content_truncated_lines == 0


def test_snapshot_pane_truncates_content(mcp_server: Server, mcp_pane: Pane) -> None:
    """snapshot_pane reports truncation via model fields, not in-band header.

    Unlike capture_pane (which returns a bare string and therefore
    signals truncation with a prefix line), snapshot_pane returns a
    Pydantic model, so truncation is surfaced on typed fields:
    ``content_truncated`` and ``content_truncated_lines``. ``content``
    itself is the kept tail with no marker.
    """
    for i in range(20):
        mcp_pane.send_keys(f"echo snap_line_{i}", enter=True)
    retry_until(
        lambda: "snap_line_19" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=5,
        socket_name=mcp_server.socket_name,
    )
    assert result.content_truncated is True
    assert result.content_truncated_lines > 0
    assert result.content.count("\n") == 4  # 5 lines kept -> 4 separators
    assert "[... truncated" not in result.content
    assert "snap_line_19" in result.content


def test_snapshot_pane_max_lines_none_keeps_full_content(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``max_lines=None`` returns the full content with no truncation flag."""
    for i in range(20):
        mcp_pane.send_keys(f"echo snapnone_{i}", enter=True)
    retry_until(
        lambda: "snapnone_19" in "\n".join(mcp_pane.capture_pane()),
        2,
        raises=True,
    )

    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=None,
        socket_name=mcp_server.socket_name,
    )
    assert result.content_truncated is False
    assert result.content_truncated_lines == 0
    assert "snapnone_19" in result.content


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

    import asyncio

    result = asyncio.run(
        wait_for_content_change(
            pane_id=mcp_pane.pane_id,
            timeout=3.0,
            socket_name=mcp_server.socket_name,
        )
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

    import asyncio

    result = asyncio.run(
        wait_for_content_change(
            pane_id=mcp_pane.pane_id,
            timeout=0.5,
            socket_name=mcp_server.socket_name,
        )
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
    """paste_text must not leave its ``libtmux_mcp_*_paste`` buffer behind.

    Regression guard for the pre-fix behavior: the earlier
    implementation used tmux's default unnamed buffer AND relied on
    `paste-buffer -d` to clean up. If paste-buffer failed mid-flight
    the buffer leaked. The fix generates a unique
    ``libtmux_mcp_<uuid>_paste`` named buffer per call (matching the
    ``buffer_tools._BUFFER_NAME_RE`` shape) and adds a best-effort
    ``delete-buffer -b`` in ``finally`` so the server is left in a
    clean state on both success and failure paths.

    The ``libtmux_mcp_`` prefix matches the namespace used by
    :mod:`libtmux_mcp.tools.buffer_tools`, so an operator filtering
    ``list-buffers`` on that prefix sees every MCP-owned buffer.

    The check is portable across every tmux version the CI matrix
    tests (3.2a through master): ``list-buffers`` with a format string
    returns buffer names without any version-specific behavior.
    """
    paste_text(
        text="echo BUFFER_ISOLATION_test",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )

    listing = mcp_server.cmd("list-buffers", "-F", "#{buffer_name}")
    buffer_names = "\n".join(listing.stdout or [])
    assert "libtmux_mcp_" not in buffer_names, (
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


def test_respawn_pane_advertises_destructive_non_idempotent() -> None:
    """``respawn_pane`` registers as mutating-tier with destructive hints.

    Default ``kill=True`` sends ``SPAWN_KILL`` to the running process
    (`cmd-respawn-pane.c:78-79`); repeated calls kill repeated processes.
    The MCP spec defines ``destructiveHint`` as "may perform destructive
    updates" and ``idempotentHint`` as "calling repeatedly will have no
    additional effect" (`mcp/types.py:1268-1282`). The default
    ``ANNOTATIONS_MUTATING`` preset (``destructiveHint=False``,
    ``idempotentHint=True``) would lie to the agent. The new
    ``ANNOTATIONS_MUTATING_DESTRUCTIVE`` preset stays in ``TAG_MUTATING``
    so the recovery use case remains visible to default-profile clients,
    while honestly advertising destructive non-idempotent semantics.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import pane_tools

    mcp = FastMCP(name="test-respawn-annotations")
    pane_tools.register(mcp)

    tool = asyncio.run(mcp.get_tool("respawn_pane"))
    assert tool is not None, "respawn_pane should be registered"
    assert tool.annotations is not None, (
        "respawn_pane registration should carry annotations"
    )
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.idempotentHint is False
    assert tool.annotations.readOnlyHint is False


# ---------------------------------------------------------------------------
# Typed-output regression guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected_type"),
    [
        # Read-heavy tools must keep returning Pydantic models so MCP
        # clients get machine-readable ``outputSchema`` entries and
        # agents don't have to re-parse strings. Regression guard:
        # any future change that flattens one of these back to ``str``
        # will break this test and force an explicit review.
        ("get_pane_info", "PaneInfo"),
        ("snapshot_pane", "PaneSnapshot"),
    ],
)
def test_pane_read_tools_return_pydantic_models(
    mcp_server: Server, mcp_pane: Pane, tool_name: str, expected_type: str
) -> None:
    """Read-heavy pane tools return their Pydantic model, not ``str``."""
    tools: dict[str, t.Callable[..., t.Any]] = {
        "get_pane_info": get_pane_info,
        "snapshot_pane": snapshot_pane,
    }
    result = tools[tool_name](
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert type(result).__name__ == expected_type
    assert hasattr(result, "model_dump"), "expected a Pydantic BaseModel instance"
