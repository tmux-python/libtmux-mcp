"""Tests for id and name resolution."""

from __future__ import annotations

import contextlib
import typing as t

import pytest
from fastmcp.exceptions import ToolError
from libtmux import exc
from libtmux.session import Session

from libtmux_mcp._resolve import (
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    tmux_id_sort_key,
)
from libtmux_mcp.tools.hook_tools import show_hooks
from libtmux_mcp.tools.option_tools import show_option
from libtmux_mcp.tools.session_tools import get_session_info, rename_session

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.window import Window


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
    # Control first: a genuinely absent session must still be absent.
    with pytest.raises(exc.TmuxObjectDoesNotExist):
        _resolve_session(mcp_server, session_name="definitely-not-here")

    from libtmux_mcp import _servers

    monkeypatch.setattr(
        _servers, "_probe_liveness", lambda _server: (False, "server exited")
    )
    with pytest.raises(ToolError, match="could not be queried"):
        _resolve_session(mcp_server, session_name="definitely-not-here")

    # And a session that IS there still resolves without probing.
    assert _resolve_session(mcp_server, session_name=mcp_session.session_name)


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
