"""Tests for libtmux MCP utilities."""

from __future__ import annotations

import contextlib
import os
import typing as t

import pytest
from fastmcp.exceptions import ToolError
from libtmux import exc
from libtmux.session import Session

from libtmux_mcp._utils import (
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    ANNOTATIONS_SHELL,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    VALID_SAFETY_LEVELS,
    _apply_filters,
    _get_server,
    _invalidate_server,
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    _serialize_pane,
    _serialize_session,
    _serialize_window,
    _server_cache,
    _unrunnable_spawn_program,
    tmux_id_sort_key,
)
from libtmux_mcp.models import SessionInfo
from libtmux_mcp.tools.hook_tools import show_hooks
from libtmux_mcp.tools.option_tools import show_option
from libtmux_mcp.tools.session_tools import get_session_info, rename_session

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.window import Window


def test_get_server_creates_server() -> None:
    """_get_server creates a Server instance."""
    server = _get_server(socket_name="test_mcp_util")
    assert server is not None
    assert server.socket_name == "test_mcp_util"


def test_get_server_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_server returns the same instance for the same socket."""
    _server_cache.clear()
    from libtmux_mcp import _utils

    # Simulate a live server so the cache is not evicted. Patched on the
    # probe rather than on ``Server.is_alive``: _get_server reads the
    # cached handle's liveness through the BOUNDED probe now, so a
    # server it cannot reach is refused rather than silently replaced.
    monkeypatch.setattr(_utils, "_probe_liveness", lambda server: (True, None))
    s1 = _get_server(socket_name="test_cache")
    s2 = _get_server(socket_name="test_cache")
    assert s1 is s2
    # Verify 3-tuple cache key includes tmux_bin
    assert (s1.socket_name, None, None) in _server_cache


def test_get_server_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_server reads LIBTMUX_SOCKET env var."""
    _server_cache.clear()
    monkeypatch.setenv("LIBTMUX_SOCKET", "env_socket")
    server = _get_server()
    assert server.socket_name == "env_socket"


def test_resolve_session_by_name(mcp_server: Server, mcp_session: Session) -> None:
    """_resolve_session finds session by name."""
    result = _resolve_session(mcp_server, session_name=mcp_session.session_name)
    assert result.session_id == mcp_session.session_id


def test_resolve_session_by_id(mcp_server: Server, mcp_session: Session) -> None:
    """_resolve_session finds session by ID."""
    result = _resolve_session(mcp_server, session_id=mcp_session.session_id)
    assert result.session_id == mcp_session.session_id


def test_resolve_session_not_found(mcp_server: Server, mcp_session: Session) -> None:
    """_resolve_session raises when session not found."""
    with pytest.raises(exc.TmuxObjectDoesNotExist):
        _resolve_session(mcp_server, session_name="nonexistent_session_xyz")


def test_resolve_session_fallback(mcp_server: Server, mcp_session: Session) -> None:
    """_resolve_session returns first session when no filter given."""
    result = _resolve_session(mcp_server)
    assert result.session_id is not None


def test_resolve_window_by_id(mcp_server: Server, mcp_window: Window) -> None:
    """_resolve_window finds window by ID."""
    result = _resolve_window(mcp_server, window_id=mcp_window.window_id)
    assert result.window_id == mcp_window.window_id


def test_resolve_window_not_found(mcp_server: Server, mcp_session: Session) -> None:
    """_resolve_window raises when window not found."""
    with pytest.raises(exc.TmuxObjectDoesNotExist):
        _resolve_window(mcp_server, window_id="@99999")


def test_resolve_pane_by_id(mcp_server: Server, mcp_pane: Pane) -> None:
    """_resolve_pane finds pane by ID."""
    result = _resolve_pane(mcp_server, pane_id=mcp_pane.pane_id)
    assert result.pane_id == mcp_pane.pane_id


def test_resolve_pane_not_found(mcp_server: Server, mcp_session: Session) -> None:
    """_resolve_pane raises when pane not found."""
    with pytest.raises(exc.PaneNotFound):
        _resolve_pane(mcp_server, pane_id="%99999")


def test_serialize_session(mcp_session: Session) -> None:
    """_serialize_session produces a SessionInfo model."""
    data = _serialize_session(mcp_session)
    assert isinstance(data, SessionInfo)
    assert data.session_id == mcp_session.session_id
    assert data.session_name is not None
    assert data.window_count >= 0


def test_serialize_window(mcp_window: Window) -> None:
    """_serialize_window produces a WindowInfo model."""
    from libtmux_mcp.models import WindowInfo

    data = _serialize_window(mcp_window)
    assert isinstance(data, WindowInfo)
    assert data.window_id is not None
    assert data.window_name is not None
    assert data.window_index is not None
    assert data.pane_count >= 0


def test_serialize_pane(mcp_pane: Pane) -> None:
    """_serialize_pane produces a PaneInfo model."""
    from libtmux_mcp.models import PaneInfo

    data = _serialize_pane(mcp_pane)
    assert isinstance(data, PaneInfo)
    assert data.pane_id is not None
    assert data.window_id is not None
    assert data.session_id is not None


def test_get_server_evicts_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_server evicts cached server when is_alive returns False."""
    _server_cache.clear()
    s1 = _get_server(socket_name="test_evict")
    # Patch is_alive to return False to simulate a dead server
    monkeypatch.setattr(s1, "is_alive", lambda: False)
    s2 = _get_server(socket_name="test_evict")
    assert s1 is not s2


def test_invalidate_server() -> None:
    """_invalidate_server removes matching entries from cache."""
    _server_cache.clear()
    _get_server(socket_name="test_inv")
    assert len(_server_cache) == 1
    _invalidate_server(socket_name="test_inv")
    assert len(_server_cache) == 0


class ApplyFiltersFixture(t.NamedTuple):
    """Test fixture for _apply_filters."""

    test_id: str
    filters: dict[str, str | bool | int] | str | None
    expected_count: int | None  # None = don't check exact count
    expect_error: bool
    error_match: str | None


APPLY_FILTERS_FIXTURES: list[ApplyFiltersFixture] = [
    ApplyFiltersFixture(
        test_id="none_returns_all",
        filters=None,
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="empty_dict_returns_all",
        filters={},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="exact_match",
        filters={"session_name": "<session_name>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="no_match_returns_empty",
        filters={"session_name": "nonexistent_xyz_999"},
        expected_count=0,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="invalid_operator",
        filters={"session_name__badop": "test"},
        expected_count=None,
        expect_error=True,
        error_match="is not a filter operator",
    ),
    # A typo'd FIELD used to return [] rather than erroring, so an empty
    # result was indistinguishable from "nothing matched".
    ApplyFiltersFixture(
        test_id="unknown_field_with_valid_operator_errors",
        filters={"nosuch_field__contains": "x"},
        expected_count=None,
        expect_error=True,
        error_match="Unknown filter field 'nosuch_field'",
    ),
    ApplyFiltersFixture(
        test_id="unknown_field_without_operator_errors",
        filters={"totally_bogus": "zzz"},
        expected_count=None,
        expect_error=True,
        error_match="Unknown filter field 'totally_bogus'",
    ),
    ApplyFiltersFixture(
        test_id="near_miss_field_suggests_alternatives",
        filters={"session_nme__contains": "x"},
        expected_count=None,
        expect_error=True,
        error_match="Did you mean: session_name",
    ),
    ApplyFiltersFixture(
        test_id="nested_traversal_still_allowed",
        filters={"active_window__window_name__contains": ""},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="contains_operator",
        filters={"session_name__contains": "<partial>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="string_filter_exact",
        filters='{"session_name": "<session_name>"}',
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="string_filter_contains",
        filters='{"session_name__contains": "<partial>"}',
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="string_filter_invalid_json",
        filters="{bad json",
        expected_count=None,
        expect_error=True,
        error_match="Invalid filters JSON",
    ),
    ApplyFiltersFixture(
        test_id="string_filter_not_object",
        filters='"just a string"',
        expected_count=None,
        expect_error=True,
        error_match="filters must be a JSON object",
    ),
    ApplyFiltersFixture(
        test_id="string_filter_array",
        filters='["not", "a", "dict"]',
        expected_count=None,
        expect_error=True,
        error_match="filters must be a JSON object",
    ),
    # window_count is an output field with no tmux attribute of that
    # name; it resolves through an alias.
    ApplyFiltersFixture(
        test_id="output_field_alias",
        filters={"window_count": "1"},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="unknown_field_names_the_output_fields",
        filters={"bogus_key": "x"},
        expected_count=None,
        expect_error=True,
        error_match="Every field this tool returns is filterable",
    ),
    # A trailing segment that is not an operator is part of the path.
    ApplyFiltersFixture(
        test_id="traversal_without_trailing_operator",
        filters={"active_pane__pane_id": "<active_pane_id>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    # ...which must not let a mistyped operator read as a path and
    # filter everything out silently.
    ApplyFiltersFixture(
        test_id="mistyped_operator_still_errors",
        filters={"session_name__containss": "<partial>"},
        expected_count=None,
        expect_error=True,
        error_match="is not a filter operator",
    ),
]


@pytest.mark.parametrize(
    ApplyFiltersFixture._fields,
    APPLY_FILTERS_FIXTURES,
    ids=[f.test_id for f in APPLY_FILTERS_FIXTURES],
)
def test_apply_filters(
    mcp_server: Server,
    mcp_session: Session,
    test_id: str,
    filters: dict[str, str | bool | int] | str | None,
    expected_count: int | None,
    expect_error: bool,
    error_match: str | None,
) -> None:
    """_apply_filters bridges dict params to QueryList.filter()."""
    # Substitute placeholders with real session name
    if isinstance(filters, str):
        session_name = mcp_session.session_name
        assert session_name is not None
        filters = filters.replace("<session_name>", session_name)
        filters = filters.replace("<partial>", session_name[:4])
    elif filters is not None:
        session_name = mcp_session.session_name
        assert session_name is not None
        resolved: dict[str, str | bool | int] = {}
        for k, v in filters.items():
            if v == "<session_name>":
                resolved[k] = session_name
            elif v == "<partial>":
                resolved[k] = session_name[:4]
            elif v == "<active_pane_id>":
                active_pane = mcp_session.active_window.active_pane
                assert active_pane is not None
                assert active_pane.pane_id is not None
                resolved[k] = active_pane.pane_id
            else:
                resolved[k] = v
        filters = resolved

    sessions = mcp_server.sessions

    if expect_error:
        with pytest.raises(ToolError, match=error_match):
            _apply_filters(sessions, filters, _serialize_session, Session, SessionInfo)
    else:
        result = _apply_filters(
            sessions, filters, _serialize_session, Session, SessionInfo
        )
        assert isinstance(result, list)
        if expected_count is not None:
            assert len(result) == expected_count
        else:
            assert len(result) >= 1


# ---------------------------------------------------------------------------
# Caller identity parsing tests
# ---------------------------------------------------------------------------


def test_get_caller_identity_parses_tmux_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_caller_identity parses TMUX as socket_path,pid,session_id."""
    from libtmux_mcp._utils import _get_caller_identity

    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,$7")
    monkeypatch.setenv("TMUX_PANE", "%3")
    caller = _get_caller_identity()
    assert caller is not None
    assert caller.socket_path == "/tmp/tmux-1000/default"
    assert caller.server_pid == 12345
    assert caller.session_id == "$7"
    assert caller.pane_id == "%3"


def test_get_caller_identity_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_caller_identity returns None when neither TMUX nor TMUX_PANE set."""
    from libtmux_mcp._utils import _get_caller_identity

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert _get_caller_identity() is None


def test_get_caller_identity_tolerant_of_malformed_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed TMUX doesn't raise — missing fields become None."""
    from libtmux_mcp._utils import _get_caller_identity

    monkeypatch.setenv("TMUX", "/tmp/sock")  # only socket, no pid/session
    monkeypatch.setenv("TMUX_PANE", "%1")
    caller = _get_caller_identity()
    assert caller is not None
    assert caller.socket_path == "/tmp/sock"
    assert caller.server_pid is None
    assert caller.session_id is None


def test_caller_is_on_server_matches_realpath(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same resolved socket path matches across symlink variants."""
    from libtmux_mcp._utils import (
        _caller_is_on_server,
        _effective_socket_path,
        _get_caller_identity,
    )

    effective = _effective_socket_path(mcp_server)
    monkeypatch.setenv("TMUX", f"{effective},1,$0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is True


def test_effective_socket_path_prefers_display_message_query(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_effective_socket_path`` asks tmux for its own socket path.

    When libtmux doesn't carry ``Server.socket_path``, the helper
    delegates to tmux via ``display-message -p '#{socket_path}'``
    before falling back to env-reconstruction. Asking tmux directly
    makes the answer authoritative — it reflects what tmux actually
    opened rather than what our process env reconstructs.

    This narrows (but does not fully close) the macOS
    ``TMUX_TMPDIR`` gap: the query itself still depends on our env
    being able to reach the server, so if the MCP process's
    ``$TMUX_TMPDIR`` diverges from the running tmux's, the query
    fails and we fall back. The full structural fix requires
    consulting the caller's ``$TMUX`` path — see ``docs/topics/safety.md``.
    """
    from libtmux_mcp._utils import _effective_socket_path

    # Clear libtmux's cached socket_path so the query path is exercised.
    monkeypatch.setattr(mcp_server, "socket_path", None)

    effective = _effective_socket_path(mcp_server)
    assert effective is not None
    # The resolved path must include the server's socket_name.
    assert mcp_server.socket_name is not None
    assert mcp_server.socket_name in effective
    # Real tmux reports an absolute path.
    assert effective.startswith("/")


def test_effective_socket_path_falls_back_when_query_fails(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``display-message`` raises, reconstruction is used.

    Guarantees the fallback path stays reachable so self-kill-guard
    logic keeps working when tmux is unreachable, misconfigured, or
    refuses the query. Without this fallback a broken tmux would
    silently disable the caller-identity check.

    Undoes the ``cmd`` monkeypatch before returning so the fixture's
    teardown ``kill-server`` call on the real method still works.
    """
    from libtmux_mcp._utils import _effective_socket_path

    def _boom(*_a: object, **_kw: object) -> object:
        msg = "display-message rejected"
        raise exc.LibTmuxException(msg)

    monkeypatch.setattr(mcp_server, "socket_path", None)
    monkeypatch.setattr(mcp_server, "cmd", _boom)
    effective = _effective_socket_path(mcp_server)
    # Restore real ``cmd`` before the fixture tears down with kill-server.
    monkeypatch.undo()

    assert effective is not None
    assert mcp_server.socket_name is not None
    assert mcp_server.socket_name in effective


def test_caller_is_on_server_rejects_different_socket(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different socket paths mean caller is on a different server."""
    from libtmux_mcp._utils import _caller_is_on_server, _get_caller_identity

    monkeypatch.setenv("TMUX", "/tmp/tmux-99999/unrelated,1,$0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is False


def test_caller_is_on_server_basename_fallback_survives_tmpdir_divergence(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Self-kill guard still blocks when ``$TMUX_TMPDIR`` diverges.

    Scenario: MCP process has the wrong ``$TMUX_TMPDIR`` (macOS under
    launchd). The ``display-message`` query fails because tmux can't
    find the socket using our env. ``_effective_socket_path`` falls
    back to env-based reconstruction, which produces a path that does
    NOT match the caller's ``$TMUX`` realpath. Without a basename
    fallback the guard would mistakenly open — but the caller's socket
    name and the target's ``socket_name`` DO still agree (they live in
    different namespaces than ``$TMUX_TMPDIR``), so the conservative
    last-chance match still fires and blocks.
    """
    from libtmux_mcp._utils import _caller_is_on_server, _get_caller_identity

    def _boom(*_a: object, **_kw: object) -> object:
        msg = "display-message rejected"
        raise exc.LibTmuxException(msg)

    # Force the display-message query path to fail by clearing the
    # cached socket_path and making cmd raise.
    monkeypatch.setattr(mcp_server, "socket_path", None)
    monkeypatch.setattr(mcp_server, "cmd", _boom)
    # Point reconstruction at a bogus tmpdir that could never match
    # the caller's path — only the basename-fallback can save us.
    monkeypatch.setenv("TMUX_TMPDIR", "/nonexistent-guard-test-tmpdir")
    # Caller's $TMUX points at the REAL tmpdir with a path whose
    # basename matches server.socket_name. Realpath comparison will
    # fail (bogus vs. real path, neither exists at /nonexistent…).
    caller_socket_path = f"/correct-tmpdir/tmux-{os.geteuid()}/{mcp_server.socket_name}"
    monkeypatch.setenv("TMUX", f"{caller_socket_path},1,$0")
    monkeypatch.setenv("TMUX_PANE", "%1")

    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is True
    # Restore real ``cmd`` before the fixture tears down with kill-server.
    monkeypatch.undo()


def test_caller_is_on_server_conservative_when_socket_unknown(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TMUX_PANE without TMUX: err on the side of blocking (True)."""
    from libtmux_mcp._utils import _caller_is_on_server, _get_caller_identity

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%1")
    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is True


def test_caller_is_on_server_none_when_not_in_tmux(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither TMUX nor TMUX_PANE set → no caller → no guard."""
    from libtmux_mcp._utils import _caller_is_on_server, _get_caller_identity

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is False


class SerializePaneCallerFixture(t.NamedTuple):
    """Test fixture for _serialize_pane is_caller annotation."""

    test_id: str
    tmux_pane_env: str | None
    use_real_pane_id: bool
    expected_is_caller: bool | None


SERIALIZE_PANE_CALLER_FIXTURES: list[SerializePaneCallerFixture] = [
    SerializePaneCallerFixture(
        # TMUX_PANE is set to the real pane id but TMUX is unset, so the
        # caller's socket cannot be verified. The strict comparator
        # declines to assume same-server: ``False`` not ``True``.
        # Pre-fixup this returned ``True`` via ``_caller_is_on_server``'s
        # conservative-True branch — a cross-socket false positive the
        # informational annotation must not carry.
        test_id="matching_pane_id_no_tmux_env",
        tmux_pane_env=None,
        use_real_pane_id=True,
        expected_is_caller=False,
    ),
    SerializePaneCallerFixture(
        test_id="non_matching_pane_id",
        tmux_pane_env="%99999",
        use_real_pane_id=False,
        expected_is_caller=False,
    ),
    SerializePaneCallerFixture(
        test_id="unset_outside_tmux",
        tmux_pane_env=None,
        use_real_pane_id=False,
        expected_is_caller=None,
    ),
]


@pytest.mark.parametrize(
    SerializePaneCallerFixture._fields,
    SERIALIZE_PANE_CALLER_FIXTURES,
    ids=[f.test_id for f in SERIALIZE_PANE_CALLER_FIXTURES],
)
def test_serialize_pane_is_caller(
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
    test_id: str,
    tmux_pane_env: str | None,
    use_real_pane_id: bool,
    expected_is_caller: bool | None,
) -> None:
    """_serialize_pane sets is_caller based on TMUX_PANE env var."""
    if use_real_pane_id:
        monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id or "")
    elif tmux_pane_env is not None:
        monkeypatch.setenv("TMUX_PANE", tmux_pane_env)
    else:
        monkeypatch.delenv("TMUX_PANE", raising=False)

    data = _serialize_pane(mcp_pane)
    assert data.is_caller is expected_is_caller


def test_serialize_pane_is_caller_false_across_sockets(
    TestServer: type[Server],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """is_caller must not flag a pane on a *different* tmux socket.

    Regression for tmux-python/libtmux-mcp#19. Before the fix,
    ``_serialize_pane`` compared ``pane.pane_id == TMUX_PANE`` without
    any socket check — so a caller inside pane ``%0`` on socket A saw
    ``is_caller=True`` for any pane with id ``%0`` on any other server.

    Two fresh libtmux servers emit matching pane ids (both start at
    ``%0``), so this reproduces the false-positive exactly. Point the
    caller at server A, serialize pane ``%0`` on server B, assert the
    annotation says ``False``.
    """
    from libtmux_mcp._utils import _effective_socket_path

    server_a = TestServer()
    session_a = server_a.new_session(session_name="mcp_issue19_a")
    pane_a = session_a.active_window.active_pane
    assert pane_a is not None and pane_a.pane_id is not None

    server_b = TestServer()
    session_b = server_b.new_session(session_name="mcp_issue19_b")
    pane_b = session_b.active_window.active_pane
    assert pane_b is not None and pane_b.pane_id is not None

    # Prerequisite: the two freshly-spawned servers emitted matching
    # pane ids. If they didn't (a tmux version quirk), the false
    # positive can't be exercised — skip rather than fail.
    if pane_a.pane_id != pane_b.pane_id:
        pytest.skip(
            f"sibling servers emitted distinct pane ids "
            f"({pane_a.pane_id} vs {pane_b.pane_id}); cannot reproduce issue #19"
        )

    socket_a = _effective_socket_path(server_a)
    assert socket_a is not None
    monkeypatch.setenv("TMUX", f"{socket_a},1,{session_a.session_id or '$0'}")
    monkeypatch.setenv("TMUX_PANE", pane_a.pane_id)

    # Pane on the *other* server — must be flagged False even though
    # its pane_id matches TMUX_PANE.
    assert _serialize_pane(pane_b).is_caller is False
    # Sanity: on the caller's own server, same pane_id *is* the caller.
    assert _serialize_pane(pane_a).is_caller is True


def test_serialize_pane_is_caller_requires_tmux_env_not_just_pane(
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TMUX_PANE`` alone must not declare a caller identity.

    Regression for the subtle cross-socket false positive that
    :func:`_caller_is_on_server`'s "socket_path unset → conservative
    True" branch would otherwise introduce. When the MCP process has
    ``TMUX_PANE`` in its environment but not ``TMUX`` — an unusual but
    possible state an agent harness can produce — the caller's socket
    is unknowable. The strict comparator declines to assert
    ``is_caller=True`` in that case so any pane whose id happens to
    match ``TMUX_PANE`` across *any* server is annotated ``False``,
    not a false positive. Exercises the code path that was left
    un-covered after the direct ``_get_caller_pane_id`` unit tests
    were removed.
    """
    assert mcp_pane.pane_id is not None
    monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id)
    monkeypatch.delenv("TMUX", raising=False)

    assert _serialize_pane(mcp_pane).is_caller is False


# ---------------------------------------------------------------------------
# Annotation and tag constants tests
# ---------------------------------------------------------------------------

_ANNOTATION_KEYS = {
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
}


def test_annotation_presets_have_correct_keys() -> None:
    """All annotation presets contain exactly the four MCP annotation keys."""
    for preset in (
        ANNOTATIONS_RO,
        ANNOTATIONS_MUTATING,
        ANNOTATIONS_CREATE,
        ANNOTATIONS_SHELL,
        ANNOTATIONS_DESTRUCTIVE,
    ):
        assert set(preset.keys()) == _ANNOTATION_KEYS


def test_annotations_ro_is_readonly() -> None:
    """ANNOTATIONS_RO marks tools as read-only."""
    assert ANNOTATIONS_RO["readOnlyHint"] is True
    assert ANNOTATIONS_RO["destructiveHint"] is False


def test_annotations_destructive_is_destructive() -> None:
    """ANNOTATIONS_DESTRUCTIVE marks tools as destructive."""
    assert ANNOTATIONS_DESTRUCTIVE["destructiveHint"] is True
    assert ANNOTATIONS_DESTRUCTIVE["readOnlyHint"] is False


def test_annotations_shell_is_open_world() -> None:
    """ANNOTATIONS_SHELL marks shell-driving tools as open-world.

    Shell-driving tools (``send_keys``, ``paste_text``, ``pipe_pane``)
    interact with arbitrary external state through whatever command the
    caller runs — the canonical open-world MCP interaction.
    """
    assert ANNOTATIONS_SHELL["openWorldHint"] is True
    assert ANNOTATIONS_SHELL["readOnlyHint"] is False
    assert ANNOTATIONS_SHELL["destructiveHint"] is False
    assert ANNOTATIONS_SHELL["idempotentHint"] is False


def test_annotations_create_is_closed_world() -> None:
    """ANNOTATIONS_CREATE does NOT set openWorldHint.

    Create-style mutating tools (``create_session``, ``create_window``,
    ``split_window``, ``swap_pane``, ``enter_copy_mode``) allocate tmux
    objects but do not interact with an open-ended environment. The
    shell-driving case is separately handled by ``ANNOTATIONS_SHELL``.
    """
    assert ANNOTATIONS_CREATE["openWorldHint"] is False


def test_tag_constants() -> None:
    """Safety tier tag constants are distinct strings."""
    tags = {TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE}
    assert len(tags) == 3


def test_valid_safety_levels_matches_tags() -> None:
    """VALID_SAFETY_LEVELS contains all tag constants."""
    assert {TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE} == VALID_SAFETY_LEVELS


# ---------------------------------------------------------------------------
# _tmux_argv tests
# ---------------------------------------------------------------------------


class _FakeServer(t.NamedTuple):
    """Minimal Server stand-in for argv-building unit tests."""

    socket_name: str | None
    socket_path: str | None
    tmux_bin: str | None = None


@pytest.mark.parametrize(
    ("server", "args", "expected"),
    [
        (
            _FakeServer(socket_name="s", socket_path=None),
            ("list-sessions",),
            ["tmux", "-L", "s", "list-sessions"],
        ),
        (
            _FakeServer(socket_name=None, socket_path="/tmp/tmux-1000/default"),
            ("ls",),
            ["tmux", "-S", "/tmp/tmux-1000/default", "ls"],
        ),
        (
            _FakeServer(socket_name="s", socket_path="/tmp/tmux-1000/s"),
            ("wait-for", "-S", "ch"),
            ["tmux", "-L", "s", "-S", "/tmp/tmux-1000/s", "wait-for", "-S", "ch"],
        ),
        (
            _FakeServer(socket_name=None, socket_path=None, tmux_bin="/opt/tmux"),
            ("show-options",),
            ["/opt/tmux", "show-options"],
        ),
    ],
)
def test_tmux_argv_honours_socket_and_binary(
    server: _FakeServer, args: tuple[str, ...], expected: list[str]
) -> None:
    """``_tmux_argv`` covers the socket_name / socket_path / tmux_bin axes."""
    from libtmux_mcp._utils import _tmux_argv

    assert _tmux_argv(t.cast("t.Any", server), *args) == expected


# ---------------------------------------------------------------------------
# Error-handler decorator tests
# ---------------------------------------------------------------------------


def test_handle_tool_errors_passes_value_through() -> None:
    """A successful sync call returns the function's result untouched."""
    from libtmux_mcp._utils import handle_tool_errors

    @handle_tool_errors
    def _ok(x: int) -> int:
        return x * 2

    assert _ok(3) == 6


def test_handle_tool_errors_translates_libtmux_exception() -> None:
    """Libtmux errors are remapped to ``ToolError``."""
    from libtmux_mcp._utils import handle_tool_errors

    err_msg = "session foo already exists"

    @handle_tool_errors
    def _raiser() -> None:
        raise exc.TmuxSessionExists(err_msg)

    with pytest.raises(ToolError, match=err_msg):
        _raiser()


def test_handle_tool_errors_preserves_existing_tool_error() -> None:
    """An explicit ``ToolError`` is not rewrapped."""
    from libtmux_mcp._utils import handle_tool_errors

    sentinel = ToolError("explicit message")

    @handle_tool_errors
    def _raiser() -> None:
        raise sentinel

    with pytest.raises(ToolError) as excinfo:
        _raiser()
    assert excinfo.value is sentinel


def test_handle_tool_errors_async_passes_value_through() -> None:
    """Successful async tools return their result normally."""
    import asyncio

    from libtmux_mcp._utils import handle_tool_errors_async

    @handle_tool_errors_async
    async def _ok(x: int) -> int:
        return x + 5

    assert asyncio.run(_ok(10)) == 15


def test_handle_tool_errors_async_translates_libtmux_exception() -> None:
    """Async libtmux errors are remapped to ``ToolError`` consistently."""
    import asyncio

    from libtmux_mcp._utils import handle_tool_errors_async

    msg = "%99"

    @handle_tool_errors_async
    async def _raiser() -> None:
        raise exc.PaneNotFound(msg)

    with pytest.raises(ToolError, match="Pane not found"):
        asyncio.run(_raiser())


def test_handle_tool_errors_async_preserves_tool_error() -> None:
    """Async tools re-raise explicit ``ToolError`` without rewrapping."""
    import asyncio

    from libtmux_mcp._utils import handle_tool_errors_async

    sentinel = ToolError("explicit async message")

    @handle_tool_errors_async
    async def _raiser() -> None:
        raise sentinel

    with pytest.raises(ToolError) as excinfo:
        asyncio.run(_raiser())
    assert excinfo.value is sentinel


def test_handle_tool_errors_async_wraps_unexpected_exception() -> None:
    """Non-libtmux exceptions are wrapped with a typed prefix."""
    import asyncio

    from libtmux_mcp._utils import handle_tool_errors_async

    msg = "boom"

    @handle_tool_errors_async
    async def _raiser() -> None:
        raise RuntimeError(msg)

    with pytest.raises(ToolError, match=r"Unexpected error: RuntimeError: boom"):
        asyncio.run(_raiser())


# ---------------------------------------------------------------------------
# ExpectedToolError log-level tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [
        exc.TmuxSessionExists("session foo already exists"),
        exc.BadSessionName("bad name"),
        exc.TmuxObjectDoesNotExist("@99"),
        exc.ObjectDoesNotExist(query={"window_name": "gone"}),
        exc.MultipleObjectsReturned(count=2, query={"pane_id": "%0"}),
        exc.PaneNotFound("%99"),
        exc.LibTmuxException("server gone"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_map_exception_expected_failures_log_at_warning(
    raised: Exception,
) -> None:
    """Agent-correctable libtmux failures map to WARNING-level errors."""
    import logging

    from libtmux_mcp._utils import ExpectedToolError, _map_exception_to_tool_error

    mapped = _map_exception_to_tool_error("some_tool", raised)
    assert isinstance(mapped, ExpectedToolError)
    assert mapped.log_level == logging.WARNING


@pytest.mark.parametrize(
    "raised",
    [
        exc.TmuxCommandNotFound("tmux missing"),
        RuntimeError("boom"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_map_exception_operator_faults_stay_at_error(raised: Exception) -> None:
    """Environment faults and unexpected bugs keep the ERROR default."""
    import logging

    from libtmux_mcp._utils import ExpectedToolError, _map_exception_to_tool_error

    mapped = _map_exception_to_tool_error("some_tool", raised)
    assert not isinstance(mapped, ExpectedToolError)
    assert mapped.log_level == logging.ERROR


class LivenessProbeFixture(t.NamedTuple):
    """Test fixture for :func:`_probe_liveness`."""

    test_id: str
    returncode: int
    stderr: list[str]
    expected_alive: bool
    expected_unreachable: str | None


LIVENESS_PROBE_FIXTURES: list[LivenessProbeFixture] = [
    LivenessProbeFixture("running", 0, [], True, None),
    LivenessProbeFixture(
        "no_daemon", 1, ["no server running on /tmp/tmux-1000/x"], False, None
    ),
    LivenessProbeFixture("missing_socket", 1, ["error connecting to /x"], False, None),
    # A live server this tmux binary cannot speak to. Reporting it the
    # same as "no server" tells the agent the user's work is gone.
    LivenessProbeFixture(
        "protocol_mismatch",
        1,
        ["server exited unexpectedly"],
        False,
        "server exited unexpectedly",
    ),
]


@pytest.mark.parametrize(
    LivenessProbeFixture._fields,
    LIVENESS_PROBE_FIXTURES,
    ids=[fixture.test_id for fixture in LIVENESS_PROBE_FIXTURES],
)
def test_probe_liveness_separates_absent_from_unreachable(
    test_id: str,
    returncode: int,
    stderr: list[str],
    expected_alive: bool,
    expected_unreachable: str | None,
) -> None:
    """Absent and unreachable are different answers.

    ``Server.is_alive()`` collapses both to False and ``Server.sessions``
    degrades to ``[]`` for both, so an ordinary tmux upgrade -- sockets
    outlive the binary that made them -- made a live server report as
    absent with no error. Driven off a fake result rather than a second
    tmux binary so the assertion does not depend on the CI tmux version.
    """
    import subprocess

    from libtmux_mcp import _utils

    assert test_id

    result = subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout="", stderr="\n".join(stderr)
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_utils, "_run_tmux_sync", lambda *a, **k: result)
        alive, unreachable = _utils._probe_liveness(t.cast("t.Any", object()))

    assert alive is expected_alive
    assert unreachable == expected_unreachable


def test_probe_liveness_reports_a_socket_that_answers_nothing() -> None:
    """Silence is a third answer, and stderr cannot carry it.

    A tmux server spinning inside its own event loop accepts the
    connection and writes nothing, so the two cases this probe exists to
    separate -- absent and unreachable -- are both wrong, and the probe
    itself used to hang on the socket it was classifying.
    """
    from libtmux_mcp import _utils

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_utils, "_run_tmux_sync", lambda *a, **k: None)
        alive, unreachable = _utils._probe_liveness(t.cast("t.Any", object()))

    assert alive is False
    assert unreachable is _utils.HUNG_SOCKET_REASON


def test_map_exception_explains_a_newline_in_a_format_value() -> None:
    """The newline-in-a-path parse failure becomes actionable.

    libtmux <= 0.62.0 splits ``-F`` output one line per object, so a
    newline inside a value breaks its strict ``zip`` and every pane on
    that server stops resolving. It arrives as a bare ``ValueError`` and
    previously reached the agent as "Unexpected error", at ERROR,
    naming nothing it could act on.
    """
    from libtmux_mcp._utils import ExpectedToolError, _map_exception_to_tool_error

    raised = ValueError("zip() argument 2 is shorter than argument 1")
    mapped = _map_exception_to_tool_error("list_panes", raised)

    assert isinstance(mapped, ExpectedToolError)
    assert "newline" in str(mapped)
    assert mapped.suggestion is not None
    assert "pane_current_path" in mapped.suggestion


def test_map_exception_does_not_double_the_pane_prefix() -> None:
    """``Pane not found: Pane not found: %9`` said it twice.

    ``exc.PaneNotFound`` already prefixes its own message, and the
    mapper prefixed it again — visible on the most frequently hit error
    in the server.
    """
    from libtmux_mcp._utils import _map_exception_to_tool_error

    raised = exc.PaneNotFound("%9999")
    assert str(raised) == "Pane not found: %9999"

    mapped = _map_exception_to_tool_error("get_pane_info", raised)

    assert str(mapped) == "Pane not found: %9999"


def test_expected_tool_error_logs_warning_through_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fastmcp's server-layer error log honors ``ExpectedToolError.log_level``.

    Uses a minimal FastMCP instance (no middleware stack) so the
    assertion isolates fastmcp's own ``Error calling tool`` record —
    the project middleware's log behavior is covered in
    ``test_middleware.py``.
    """
    import asyncio
    import logging

    from fastmcp import Client, FastMCP

    from libtmux_mcp._utils import ExpectedToolError

    probe = FastMCP(name="probe")

    @probe.tool
    def fail_expected() -> str:
        msg = "Pane not found: %99"
        raise ExpectedToolError(msg)

    async def _call() -> None:
        async with Client(probe) as client:
            await client.call_tool("fail_expected", raise_on_error=False)

    with caplog.at_level(logging.DEBUG):
        asyncio.run(_call())

    records = [r for r in caplog.records if "Error calling tool" in r.message]
    assert records, "expected fastmcp to log the tool failure"
    assert all(r.levelno == logging.WARNING for r in records)


@pytest.mark.parametrize(
    ("raised", "expected_suggestion_fragment"),
    [
        (exc.TmuxObjectDoesNotExist("@99"), "list_sessions / list_windows"),
        (
            exc.MultipleObjectsReturned(count=2, query={"pane_id": "%0"}),
            "Target it by id",
        ),
        (exc.PaneNotFound("%99"), "list_panes"),
        (exc.TmuxSessionExists("dup"), None),
        (exc.BadSessionName("bad:name"), None),
        (exc.LibTmuxException("transient"), None),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v),
)
def test_map_exception_suggestion_policy(
    raised: Exception,
    expected_suggestion_fragment: str | None,
) -> None:
    """Only the not-found branches carry agent-facing recovery hints.

    Discovery tools are the canonical fix for stale/guessed ids — the
    most common agent mistake. The other expected branches stay
    hint-free until real transcripts show agents flailing on them.
    """
    from libtmux_mcp._utils import _map_exception_to_tool_error

    mapped = _map_exception_to_tool_error("some_tool", raised)
    suggestion = getattr(mapped, "suggestion", None)
    if expected_suggestion_fragment is None:
        assert suggestion is None
    else:
        assert suggestion is not None
        assert expected_suggestion_fragment in suggestion


def test_resolve_session_does_not_call_an_unreachable_server_empty(
    monkeypatch: pytest.MonkeyPatch, mcp_server: Server, mcp_session: Session
) -> None:
    """An empty enumeration is not evidence the session is gone.

    ``server.sessions`` swallows a query failure and yields ``[]``, so
    the resolver turning "not in the list" into "does not exist"
    asserted the session was GONE when the server merely could not be
    asked. ``rename_session`` reported a running session missing, which
    invites recreating it under the same name.
    """
    from libtmux_mcp import _utils

    # Control first: a genuinely absent session must still be absent.
    with pytest.raises(exc.TmuxObjectDoesNotExist):
        _resolve_session(mcp_server, session_name="definitely-not-here")

    monkeypatch.setattr(
        _utils, "_probe_liveness", lambda _server: (False, "server exited")
    )
    with pytest.raises(ToolError, match="could not be queried"):
        _resolve_session(mcp_server, session_name="definitely-not-here")

    # And a session that IS there still resolves without probing.
    assert _resolve_session(mcp_server, session_name=mcp_session.session_name)


def test_unrunnable_spawn_program_only_decides_what_it_can() -> None:
    """The pre-flight must refuse nothing sh would have run.

    tmux passes a one-argument command to ``$SHELL -c``, so shell
    syntax is beyond a pre-flight's reach. An earlier version checked
    ``shlex.split(shell)[0]`` against PATH and refused ``cd /tmp &&
    sleep 60``, ``VAR=1 sleep 60`` and ``exec sleep 60`` -- all three
    run -- while asserting the pane would die.
    """
    undecidable_or_fine = [
        "sleep 60",
        "/bin/sh",
        "cd /tmp && sleep 60",
        "VAR=1 sleep 60",
        "exec sleep 60",
        "echo hi; sleep 60",
        "",
    ]
    for shell in undecidable_or_fine:
        assert _unrunnable_spawn_program(shell) is None, shell

    for shell in ("/no/such/shell-xyz", "definitely-not-on-path-xyz", "-k"):
        assert _unrunnable_spawn_program(shell) == shell


def test_tmux_id_sort_key_orders_past_nine() -> None:
    """A string sort calls ``$10`` older than ``$9``.

    It only goes wrong once ids pass nine -- on a long-lived server,
    which is where the wrong "oldest session" would go unnoticed
    longest.
    """
    ids = ["$9", "$10", "$11"]
    assert min(ids) == "$10"
    assert min(ids, key=tmux_id_sort_key) == "$9"


def test_untargeted_reads_pick_one_object_and_keep_it(
    mcp_server: Server, mcp_session: Session
) -> None:
    """No target meant two different things, and neither stayed put.

    The tools split by layer: the option and hook family omitted ``-t``
    and let tmux resolve by ``activity_time``, which moves whenever a
    pane produces output, while everything else took the first LISTED
    object -- and tmux lists sessions by NAME, so renaming one silently
    redirected every later untargeted call into a different session.

    Both properties are asserted, because either alone is satisfiable
    by a rule that is still wrong: agreement without stability is one
    rule that a rename moves, stability without agreement is two rules
    that happen to sit still.
    """
    socket = mcp_server.socket_name
    made = [
        mcp_server.new_session(name, window_command="sleep 300")
        for name in ("zzz_last", "aaa_first")
    ]
    activity_pane = made[-1].active_window.active_pane
    assert activity_pane is not None
    activity_pane.send_keys("true", enter=True)

    try:
        oldest_session = min(
            mcp_server.sessions, key=lambda s: tmux_id_sort_key(s.session_id)
        )
        oldest = oldest_session.session_id
        assert oldest not in {s.session_id for s in made}, (
            "fixture: the sessions created here must not be the oldest"
        )

        # Every untargeted read names the same object, whichever layer
        # used to answer it. The activity winner is the newest session,
        # so tmux's rule would give a different answer here.
        assert show_hooks(socket_name=socket).resolved_target == oldest
        assert get_session_info(socket_name=socket).session_id == oldest
        assert show_option(option="status", socket_name=socket).resolved_target == (
            oldest_session.session_name
        )

        # A rename does not move it -- the property list order could not
        # offer at any sort key, since tmux lists sessions by name.
        rename_session(
            new_name="mmm_middle", session_name="aaa_first", socket_name=socket
        )
        assert get_session_info(socket_name=socket).session_id == oldest
        assert show_hooks(socket_name=socket).resolved_target == oldest
    finally:
        for session in made:
            with contextlib.suppress(Exception):
                session.kill()


def test_validation_refusals_echo_what_the_caller_sent(mcp_server: Server) -> None:
    """A refusal that states a rule without the value is half an answer.

    Most of this tree already echoes -- ``offset``, ``limit``,
    ``scroll_up`` and the batch ``timeout`` all say "received X". These
    five did not, which is the same one-tool-does-one-thing asymmetry
    the rest of this branch has been closing. A caller who typo'd
    ``"STOP"`` was told the rule and left to spot their own mistake.

    One test over the set rather than one per message: the property is
    shared, and five near-identical tests would be bloat.
    """
    import asyncio

    from libtmux_mcp.models import SendKeysOperation
    from libtmux_mcp.tools.hook_tools import show_hooks
    from libtmux_mcp.tools.option_tools import show_option
    from libtmux_mcp.tools.pane_tools.io import run_command, send_keys_batch
    from libtmux_mcp.tools.pane_tools.layout import resize_pane

    socket = mcp_server.socket_name
    cases: list[tuple[t.Callable[[], object], str]] = [
        (
            lambda: send_keys_batch(
                operations=[SendKeysOperation(pane_id="%0", keys="x")],
                on_error=t.cast("t.Any", "STOP"),
                socket_name=socket,
            ),
            "'STOP'",
        ),
        (
            lambda: asyncio.run(
                run_command(
                    command="true", pane_id="%0", timeout=-1, socket_name=socket
                )
            ),
            "-1",
        ),
        (
            lambda: show_hooks(target="nope", socket_name=socket),
            "'nope'",
        ),
        (
            lambda: show_option(option="status", target="nope", socket_name=socket),
            "'nope'",
        ),
        (
            lambda: resize_pane(pane_id="%0", zoom=True, height=10, socket_name=socket),
            "zoom=True",
        ),
    ]
    for call, expected in cases:
        with pytest.raises(ToolError) as excinfo:
            call()
        assert expected in str(excinfo.value), (
            f"refusal did not echo {expected}: {excinfo.value}"
        )


def test_every_libtmux_tmux_cmd_call_site_is_bounded() -> None:
    """A new libtmux call site must fail loudly, not silently unbind.

    The bound is installed by rebinding ``tmux_cmd`` in each libtmux
    module that constructs one. That is invisible to the type checker
    and to import-time errors, so an upgrade adding a call site in a
    fourth module would quietly restore the unbounded path that let
    ``break_pane`` hang for 150s. AST rather than text search: the
    ``tmux_cmd(...)`` lines in ``options.py`` are doctest examples
    inside docstrings, and only a parser can tell those from calls.

    This covers list COMPLETENESS -- it fires on a libtmux upgrade. That
    the list actually drives the binding is covered by the half-wedge
    regression test, which walks ``window.panes`` and so goes through
    ``neo``; that one fires on a refactor here.
    """
    import ast
    import importlib
    import pathlib

    import libtmux

    from libtmux_mcp._utils import _PATCHED_LIBTMUX_MODULES, _BoundedTmuxCmd

    root = pathlib.Path(libtmux.__file__).parent
    callers: set[str] = set()
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - defensive
            continue
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "tmux_cmd"
            for node in ast.walk(tree)
        ):
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            callers.add(".".join(["libtmux", *parts]))

    assert callers, "found no tmux_cmd call sites; the AST walk is broken"
    assert callers == set(_PATCHED_LIBTMUX_MODULES), (
        f"libtmux constructs tmux_cmd in {sorted(callers)} but only "
        f"{sorted(_PATCHED_LIBTMUX_MODULES)} are bounded"
    )
    for name in callers:
        module = importlib.import_module(name)
        assert module.tmux_cmd is _BoundedTmuxCmd, f"{name} is unbounded"


def test_bounded_tmux_cmd_matches_stock_output(mcp_server: Server) -> None:
    """The bounded replacement must answer exactly as stock does.

    Covers the three shapes that differ: a normal command, a command
    that fails on stderr, and ``has-session``, which libtmux reports
    through *stdout* rather than stderr.
    """
    from libtmux_mcp._utils import _BoundedTmuxCmd

    stock = _BoundedTmuxCmd.__bases__[0]
    socket_flag = f"-L{mcp_server.socket_name}"
    cases = (
        (socket_flag, "list-sessions", "-F", "#{session_id}"),
        (socket_flag, "list-panes", "-t", "%999999"),
        (socket_flag, "has-session", "-t", "definitely-absent"),
    )
    for args in cases:
        mine = _BoundedTmuxCmd(*args)
        theirs = stock(*args)
        assert (mine.returncode, mine.stdout, mine.stderr) == (
            theirs.returncode,
            theirs.stdout,
            theirs.stderr,
        ), f"diverged on {args[1]}"
