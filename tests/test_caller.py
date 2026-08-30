"""Tests for caller identity and the self-kill guard."""

from __future__ import annotations

import os
import typing as t

import pytest
from libtmux import exc
from libtmux.test.retry import retry_until

if t.TYPE_CHECKING:
    from libtmux.server import Server


# ---------------------------------------------------------------------------
# Caller identity parsing tests
# ---------------------------------------------------------------------------


def test_get_caller_identity_parses_tmux_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_get_caller_identity parses TMUX as socket_path,pid,session_id."""
    from libtmux_mcp._caller import _get_caller_identity

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
    from libtmux_mcp._caller import _get_caller_identity

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert _get_caller_identity() is None


def test_get_caller_identity_tolerant_of_malformed_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed TMUX doesn't raise — missing fields become None."""
    from libtmux_mcp._caller import _get_caller_identity

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
    from libtmux_mcp._caller import (
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
    from libtmux_mcp._caller import _effective_socket_path

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
    from libtmux_mcp._caller import _effective_socket_path

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
    from libtmux_mcp._caller import _caller_is_on_server, _get_caller_identity

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
    from libtmux_mcp._caller import _caller_is_on_server, _get_caller_identity

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
    from libtmux_mcp._caller import _caller_is_on_server, _get_caller_identity

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%1")
    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is True


def test_caller_is_on_server_none_when_not_in_tmux(
    mcp_server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither TMUX nor TMUX_PANE set → no caller → no guard."""
    from libtmux_mcp._caller import _caller_is_on_server, _get_caller_identity

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert _caller_is_on_server(mcp_server, _get_caller_identity()) is False


def test_caller_is_on_server_blocks_a_nested_self_kill(
    TestServer: type[Server],
) -> None:
    """``$TMUX`` names only the INNERMOST server.

    Run an agent inside tmux and point it at a second tmux, and the pane
    hosting its terminal belongs to the OUTER server while ``$TMUX``
    describes the inner one. Every path comparison then says "different
    server" and a kill of that pane is permitted -- taking the caller's
    tty with it. Reproduced on 3.7c before the fix: guard vs the inner
    server True, vs the outer server False.

    The control matters as much as the case: an unrelated third server
    must still be killable, or the guard has simply stopped answering.

    Which row each mutation falsifies, since "it failed when I broke
    it" is a claim about the mutation chosen:

    * nesting check removed        -> the OUTER row fails
    * nesting check always True    -> the CONTROL row fails
    * primary realpath match broken -> nothing fails; the inner row is
      satisfied by a fallback route, so it does not isolate that path.
      ``test_caller_is_on_server_matches_realpath`` is what covers it.
    """
    from libtmux_mcp._caller import (
        CallerIdentity,
        _caller_is_on_server,
        _effective_socket_path,
    )

    outer, inner, other = TestServer(), TestServer(), TestServer()
    outer.new_session(session_name="o", window_command="sh")
    inner.new_session(session_name="i", window_command="sh")
    other.new_session(session_name="x", window_command="sh")

    pane = outer.sessions[0].windows[0].panes[0]
    pane.send_keys(f"tmux -L {inner.socket_name} attach -t i", enter=True)
    retry_until(
        lambda: bool(inner.cmd("list-clients", "-F", "#{client_tty}").stdout),
        10,
        raises=True,
    )
    # The nesting is real: the inner client occupies the outer pane.
    assert inner.cmd("list-clients", "-F", "#{client_tty}").stdout == [pane.pane_tty]

    caller = CallerIdentity(
        socket_path=_effective_socket_path(inner),
        server_pid=None,
        session_id=None,
        pane_id="%0",
    )
    assert _caller_is_on_server(inner, caller) is True
    assert _caller_is_on_server(outer, caller) is True
    assert _caller_is_on_server(other, caller) is False
