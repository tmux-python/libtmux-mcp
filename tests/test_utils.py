"""Tests for libtmux MCP utilities."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError
from libtmux import exc

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
    _get_caller_pane_id,
    _get_server,
    _invalidate_server,
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    _serialize_pane,
    _serialize_session,
    _serialize_window,
    _server_cache,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session
    from libtmux.window import Window


def test_get_server_creates_server() -> None:
    """_get_server creates a Server instance."""
    server = _get_server(socket_name="test_mcp_util")
    assert server is not None
    assert server.socket_name == "test_mcp_util"


def test_get_server_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_server returns the same instance for the same socket."""
    _server_cache.clear()
    s1 = _get_server(socket_name="test_cache")
    # Simulate a live server so the cache is not evicted
    monkeypatch.setattr(s1, "is_alive", lambda: True)
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
    from libtmux_mcp.models import SessionInfo

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
    filters: dict[str, str] | str | None
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
        error_match="Invalid filter operator",
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
    filters: dict[str, str] | str | None,
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
        resolved: dict[str, str] = {}
        for k, v in filters.items():
            if v == "<session_name>":
                resolved[k] = session_name
            elif v == "<partial>":
                resolved[k] = session_name[:4]
            else:
                resolved[k] = v
        filters = resolved

    sessions = mcp_server.sessions

    if expect_error:
        with pytest.raises(ToolError, match=error_match):
            _apply_filters(sessions, filters, _serialize_session)
    else:
        result = _apply_filters(sessions, filters, _serialize_session)
        assert isinstance(result, list)
        if expected_count is not None:
            assert len(result) == expected_count
        else:
            assert len(result) >= 1


# ---------------------------------------------------------------------------
# _get_caller_pane_id / _serialize_pane is_caller tests
# ---------------------------------------------------------------------------


def test_get_caller_pane_id_returns_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_caller_pane_id returns TMUX_PANE when set."""
    monkeypatch.setenv("TMUX_PANE", "%42")
    assert _get_caller_pane_id() == "%42"


def test_get_caller_pane_id_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_caller_pane_id returns None outside tmux."""
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert _get_caller_pane_id() is None


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


def test_caller_is_on_server_rejects_different_socket(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different socket paths mean caller is on a different server."""
    from libtmux_mcp._utils import _caller_is_on_server, _get_caller_identity

    monkeypatch.setenv("TMUX", "/tmp/tmux-99999/unrelated,1,$0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is False


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
        test_id="matching_pane_id",
        tmux_pane_env=None,
        use_real_pane_id=True,
        expected_is_caller=True,
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
