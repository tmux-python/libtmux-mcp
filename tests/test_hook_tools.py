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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "bug: show_hooks(scope='server') misses pane-level hooks set via "
        "set-hook -g (which tmux stores in the global-window options tree, "
        "enumerated by show-hooks -gw, not -g). show_hook(hook_name=..., "
        "scope='server') finds them because it runs a name-targeted lookup. "
        "Fix lands in the next commit."
    ),
)
def test_show_hooks_surfaces_globally_set_pane_hook(
    mcp_server: Server, mcp_session: Session
) -> None:
    """show_hooks and show_hook must agree on -g-set pane-level hooks.

    Regression guard for the multi-agent-test finding. Repro:

    1. ``tmux set-hook -g pane-focus-in 'display-message ...'`` stores
       the hook in tmux's global-window options tree (because
       ``pane-focus-in`` is a pane-level hook).
    2. ``show_hook(hook_name='pane-focus-in', scope='server')`` finds
       it because tmux's name-targeted lookup consults the correct
       tree per hook name.
    3. ``show_hooks(scope='server')`` returns no entry for
       ``pane-focus-in`` because our tool currently maps
       ``scope='server'`` to ``-g`` only (global-session tree), missing
       the global-window tree that holds pane/window-level hooks.

    The two tools share ``_resolve_hook_target`` but diverge at the
    tmux CLI level: ``show-hooks -g NAME`` works for any named hook;
    ``show-hooks -g`` (no name) only lists one tree. The fix makes
    ``show_hooks(scope='server')`` enumerate both trees so the
    invariant ``show_hook ⊆ show_hooks`` holds for every scope.
    """
    mcp_server.cmd("set-hook", "-g", "pane-focus-in", "display-message xfail_probe")
    try:
        singular = show_hook(
            hook_name="pane-focus-in",
            scope="server",
            socket_name=mcp_server.socket_name,
        )
        plural = show_hooks(
            scope="server",
            socket_name=mcp_server.socket_name,
        )

        # Control: show_hook finds the -g-set pane hook.
        singular_names = {e.hook_name for e in singular.entries}
        assert "pane-focus-in" in singular_names, (
            "show_hook could not find -g-set pane-focus-in; test setup failed"
        )

        # The bug: show_hooks misses what show_hook sees.
        plural_names = {e.hook_name for e in plural.entries}
        assert "pane-focus-in" in plural_names, (
            f"show_hooks(scope='server') returned {plural_names} "
            f"but show_hook found pane-focus-in — inconsistency."
        )
    finally:
        mcp_server.cmd("set-hook", "-g", "-u", "pane-focus-in")
    _ = mcp_session  # session fixture ensures the server has a usable target


def test_tmux_splits_global_hooks_across_session_and_window_trees(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Document tmux's two-tree model for ``set-hook -g`` storage.

    Diagnostic test: tmux stores a ``-g``-set hook in whichever global
    options tree matches the hook's scope. Session-level hooks (e.g.
    ``session-closed``) go into the global-session tree enumerated by
    ``show-hooks -g``. Pane / window-level hooks (e.g.
    ``pane-focus-in``) go into the global-window tree enumerated by
    ``show-hooks -gw``. ``show-hooks -g`` (no ``-w``) only lists the
    first tree — which is the root cause of the ``show_hooks``
    inconsistency guarded above. Pinning this behaviour here means a
    future tmux change that unifies the trees (or breaks this
    contract) surfaces as a test failure rather than a silent drift.
    """
    mcp_server.cmd("set-hook", "-g", "session-closed", "display-message SESS")
    mcp_server.cmd("set-hook", "-g", "pane-focus-in", "display-message PANE")
    try:
        session_tree = "\n".join(mcp_server.cmd("show-hooks", "-g").stdout)
        window_tree = "\n".join(mcp_server.cmd("show-hooks", "-gw").stdout)

        assert "session-closed[" in session_tree
        assert "pane-focus-in" not in session_tree, (
            "tmux now lists pane-focus-in in the global-session tree — "
            "the two-tree assumption behind show_hooks(scope='server') "
            "may need revisiting."
        )
        assert "pane-focus-in[" in window_tree
    finally:
        mcp_server.cmd("set-hook", "-g", "-u", "session-closed")
        mcp_server.cmd("set-hook", "-g", "-u", "pane-focus-in")
    _ = mcp_session


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
