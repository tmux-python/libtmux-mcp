"""Tests for read-only tmux hook introspection tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp.models import HookListResult
from libtmux_mcp.tools.hook_tools import show_hook, show_hooks

if t.TYPE_CHECKING:
    from libtmux.server import Server
    from libtmux.session import Session


def test_show_hooks_returns_hook_list_result(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``show_hooks`` returns a :class:`HookListResult`, even when empty."""
    result = show_hooks(
        scope="session",
        target=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, HookListResult)
    # Most fresh sessions have no hooks set — accept empty or a few
    # defaults depending on the tmux build. Shape is what matters here.
    assert isinstance(result.entries, list)


def test_show_hook_roundtrip_via_set_hook(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Setting a hook via libtmux and reading back via show_hook matches."""
    mcp_session.set_hook("pane-exited", "display-message MCP_HOOK_TEST")
    try:
        result = show_hook(
            hook_name="pane-exited",
            scope="session",
            target=mcp_session.session_name,
            socket_name=mcp_server.socket_name,
        )
        commands = [entry.command for entry in result.entries]
        assert any("MCP_HOOK_TEST" in cmd for cmd in commands)
    finally:
        mcp_session.unset_hook("pane-exited")


def test_show_hook_unset_known_hook_returns_empty(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A valid hook name that has been unset yields an empty result.

    ``pane-exited`` is a real hook shipping with tmux. Setting and
    then immediately unsetting it reproduces the "hook is unset"
    shape: tmux errors with ``"too many arguments"`` and the wrapper
    translates that to ``HookListResult(entries=[])``.
    """
    mcp_session.set_hook("pane-exited", "display-message TEMP")
    mcp_session.unset_hook("pane-exited")
    result = show_hook(
        hook_name="pane-exited",
        scope="session",
        target=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert result.entries == []


def test_show_hook_unknown_name_raises(
    mcp_server: Server, mcp_session: Session
) -> None:
    """A hook name tmux doesn't recognise surfaces as ToolError.

    Regression guard: the pre-fix ``show_hook`` swallowed ``"unknown
    hook"`` / ``"invalid option"`` alongside ``"too many arguments"``,
    hiding typos and wrong-scope mistakes behind an empty result. The
    narrowed handler now keeps only ``"too many arguments"`` (the
    canonical "hook is unset" signal) and surfaces every other tmux
    error so agents can correct their input.
    """
    with pytest.raises(ToolError, match=r"invalid option|unknown hook"):
        show_hook(
            hook_name="after-nonexistent-hook-cxyz",
            scope="session",
            target=mcp_session.session_name,
            socket_name=mcp_server.socket_name,
        )


def test_show_hooks_invalid_scope(mcp_server: Server) -> None:
    """Unknown scope value is rejected with a helpful ToolError."""
    with pytest.raises(ToolError, match="Invalid scope"):
        show_hooks(
            scope=t.cast("t.Any", "cluster"),
            socket_name=mcp_server.socket_name,
        )


def test_show_hooks_target_without_scope(mcp_server: Server) -> None:
    """Passing ``target`` without ``scope`` is rejected."""
    with pytest.raises(ToolError, match="scope is required"):
        show_hooks(
            target="somesession",
            socket_name=mcp_server.socket_name,
        )
