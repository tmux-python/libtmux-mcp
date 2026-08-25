"""Test fixtures for libtmux MCP server tests."""

from __future__ import annotations

import contextlib
import os
import pathlib
import time
import typing as t

import pytest
from libtmux.server import Server as _Server

from libtmux_mcp._utils import _server_cache

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session
    from libtmux.window import Window

#: A socket this old cannot belong to a run that is still going: the
#: whole suite takes about two minutes. Age-gating matters because
#: pytest-xdist workers all start at once, and an unconditional reaper
#: in one worker would kill the servers another worker just created.
_ABANDONED_SOCKET_AGE_SECONDS = 3600


def pytest_sessionstart(session: pytest.Session) -> None:
    """Reap tmux daemons left behind by runs that were killed.

    Fixture finalizers do not run when pytest is SIGKILLed or the
    machine goes down, so an interrupted run leaks a live tmux daemon
    and its socket permanently, and they accumulate: measured 119 live
    servers spanning three days on one development box. A clean run
    leaks none -- verified before/after -- so this is purely about
    interrupted ones.

    It is not only untidy. ``list_servers`` probes every live socket, so
    the debris makes that tool slower on every call for as long as it
    sits there.
    """
    tmpdir = pathlib.Path(os.environ.get("TMUX_TMPDIR", "/tmp"))
    uid_dir = tmpdir / f"tmux-{os.geteuid()}"
    if not uid_dir.is_dir():
        return
    cutoff = time.time() - _ABANDONED_SOCKET_AGE_SECONDS
    for entry in uid_dir.glob("libtmux_test*"):
        try:
            if not entry.is_socket() or entry.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        with contextlib.suppress(Exception):
            _Server(socket_name=entry.name).kill()
        with contextlib.suppress(OSError):
            entry.unlink()


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


def wire_annotations(tool: t.Any) -> dict[str, t.Any]:
    """Return a tool's annotations keyed by their PROTOCOL names.

    Reading ``tool.annotations.readOnlyHint`` asserts the SDK's Python
    attribute spelling, which is not stable: ``mcp`` 2.x renamed every
    hint to snake_case while keeping the camelCase wire alias. fastmcp
    ships a deprecation shim so the old spelling still resolves at
    runtime, which means a suite can keep passing while ``mypy`` fails
    and the codebase quietly depends on a shim.

    Dumping ``by_alias=True`` asserts the names that actually go on the
    wire, which is what a client sees and the only thing the protocol
    guarantees. Same idiom as ``tools/batch_tools.py``.
    """
    annotations = tool.annotations
    assert annotations is not None, f"{tool.name} carries no annotations"
    dumped: dict[str, t.Any] = annotations.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    return dumped
