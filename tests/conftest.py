"""Test fixtures for libtmux MCP server tests."""

from __future__ import annotations

import contextlib
import os
import pathlib
import typing as t

import pytest
from libtmux.server import Server

from libtmux_mcp._utils import _server_cache

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.session import Session
    from libtmux.window import Window


@pytest.fixture(scope="session", autouse=True)
def _reap_leaked_libtmux_test_sockets() -> t.Generator[None, None, None]:
    """Reap leaked ``libtmux_test*`` daemons and socket files post-suite.

    libtmux's pytest plugin creates per-test tmux servers on
    ``libtmux_test<N>`` sockets but does not reliably kill the daemons
    or ``unlink`` the socket files on teardown — see
    `tmux-python/libtmux#660 <https://github.com/tmux-python/libtmux/issues/660>`_.
    Without this finalizer ``/tmp/tmux-<uid>/`` accumulates hundreds of
    stale socket entries across test runs (10k+ on long-lived dev
    machines per the #20 report).

    Scope is ``session``: runs after every ``pytest`` invocation. Prefix
    match on ``libtmux_test`` only — matches the literal prefix set by
    libtmux's ``pytest_plugin.py`` and never touches the developer's
    real ``default`` socket or any non-test socket. Safe under ``xdist``:
    each worker is its own pytest session and the socket operations
    (``kill_server`` / ``unlink``) are idempotent.
    """
    yield

    # ``geteuid`` is Unix-only; the tmux server socket directory only
    # exists on POSIX. Skip on platforms without it rather than erroring.
    if not hasattr(os, "geteuid"):
        return

    tmpdir = pathlib.Path(f"/tmp/tmux-{os.geteuid()}")
    if not tmpdir.is_dir():
        return

    for socket_path in tmpdir.glob("libtmux_test*"):
        # Defensive cleanup: if the server is still alive, kill it; then
        # unlink the socket file whether or not kill succeeded (tmux
        # sometimes leaves the file on disk after the daemon exits).
        # Any step may fail because the socket has already vanished,
        # permissions changed, or a concurrent run raced us — none of
        # that is actionable here, so swallow the error and move on.
        with contextlib.suppress(Exception):
            server = Server(socket_name=socket_path.name)
            if server.is_alive():
                server.kill()
        with contextlib.suppress(OSError):
            socket_path.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _clear_server_cache() -> t.Generator[None, None, None]:
    """Clear the MCP server cache between tests."""
    _server_cache.clear()
    yield
    _server_cache.clear()


@pytest.fixture(autouse=True)
def _isolate_tmux_caller_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove TMUX / TMUX_PANE so host terminal doesn't leak into tests.

    Without this, running the suite inside tmux would make caller-identity
    checks see the developer's real socket and break self-protection
    tests non-deterministically. Tests that want to exercise the guards
    set these explicitly.
    """
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)


@pytest.fixture
def mcp_server(server: Server) -> Server:
    """Provide a libtmux Server pre-registered in the MCP cache.

    This fixture sets up the server cache so MCP tools can find the
    test server without environment variables.
    """
    cache_key = (server.socket_name, None, None)
    _server_cache[cache_key] = server
    # Also register as default for tools that don't specify a socket
    _server_cache[(None, None, None)] = server
    return server


@pytest.fixture
def mcp_session(mcp_server: Server, session: Session) -> Session:
    """Provide a session accessible via MCP tools."""
    return session


@pytest.fixture
def mcp_window(mcp_session: Session) -> Window:
    """Provide a window accessible via MCP tools."""
    return mcp_session.active_window


@pytest.fixture
def mcp_pane(mcp_window: Window) -> Pane:
    """Provide a pane accessible via MCP tools."""
    active_pane = mcp_window.active_pane
    assert active_pane is not None
    return active_pane
