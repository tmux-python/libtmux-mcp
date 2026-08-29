"""Tests for libtmux objects to pydantic models."""

from __future__ import annotations

import typing as t

import pytest
from libtmux.session import Session

from libtmux_mcp._serialize import (
    _serialize_pane,
    _serialize_session,
    _serialize_window,
)
from libtmux_mcp.models import SessionInfo

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.window import Window


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
    from libtmux_mcp._caller import _effective_socket_path

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
