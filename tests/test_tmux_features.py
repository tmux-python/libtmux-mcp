"""Optional feature refusal leaves baseline MCP operations available."""

from __future__ import annotations

import asyncio
import subprocess
import typing as t

import pytest
from fastmcp.exceptions import ToolError
from libtmux import exc
from libtmux._internal import support

from libtmux_mcp import _tmux_proc
from libtmux_mcp._utils import _tmux_argv
from libtmux_mcp.tools.buffer_tools import delete_buffer, load_buffer, show_buffer
from libtmux_mcp.tools.pane_tools.io import paste_text
from libtmux_mcp.tools.window_tools import select_layout

if t.TYPE_CHECKING:
    from libtmux import Pane, Server


def test_backend_requires_command_policy_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A libtmux without the command policy cannot qualify the owned helpers."""
    monkeypatch.delattr(support, "require_commands")

    def forbidden(*args: object, **kwargs: object) -> t.NoReturn:
        pytest.fail("an unqualified backend spawned a native child")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(exc.LibTmuxException, match="version check is unavailable"):
        _tmux_proc._run_tmux_sync_bounded(["tmux", "list-sessions"], timeout=1)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "command",
    [["delete-buffer", "-b", "missing"], ["setb", "-b", "missing", "-n", "renamed"]],
)
def test_owned_helpers_enforce_named_buffer_identity(
    mcp_server: Server, mcp_pane: Pane, asynchronous: bool, command: list[str]
) -> None:
    """Owned child paths share the parent literal-command policy."""
    mcp_server.set_buffer("keep", buffer_name="user")
    argv = _tmux_argv(mcp_server, *command)

    def invoke() -> object:
        if asynchronous:
            return asyncio.run(_tmux_proc._run_tmux_bounded(argv, timeout=5))
        return _tmux_proc._run_tmux_sync_bounded(argv, timeout=5, check=False)

    if not support.is_at_least(mcp_server._get_version(), "3.4"):
        with pytest.raises(exc.VersionTooLow, match=r"3\.4"):
            invoke()
    else:
        result = invoke()
        assert (
            result[0] if isinstance(result, tuple) else t.cast(t.Any, result).returncode
        ) != 0
    assert mcp_server.show_buffer(buffer_name="user") == "keep"


def test_delete_tool_preserves_user_buffer_when_named_target_is_missing(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A missing agent name must never delete the most recent user buffer."""
    mcp_server.set_buffer("retained", buffer_name="human")
    missing = "libtmux_mcp_" + "a" * 32 + "_absent"
    expected = (
        "3.4"
        if not support.is_at_least(mcp_server._get_version(), "3.4")
        else "unknown buffer"
    )
    with pytest.raises(ToolError, match=expected):
        delete_buffer(missing, socket_name=mcp_server.socket_name)
    assert mcp_server.show_buffer(buffer_name="human") == "retained"


def test_managed_paste_checks_cleanup_before_staging(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed paste cleanup is eligible before a temporary native buffer exists."""
    mcp_server.set_buffer("retained", buffer_name="human")
    entered: list[bool] = []

    def failed_paste(*args: object, **kwargs: object) -> t.NoReturn:
        entered.append(True)
        msg = "intentional paste failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(type(mcp_pane), "paste_buffer", failed_paste)
    expected = (
        "3.4"
        if not support.is_at_least(mcp_server._get_version(), "3.4")
        else "intentional paste failure"
    )
    with pytest.raises(ToolError, match=expected):
        paste_text(
            "never-entered",
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    assert entered == ([] if expected == "3.4" else [True])
    assert mcp_server.show_buffer(buffer_name="human") == "retained"
    assert mcp_server.cmd("list-buffers", "-F", "#{buffer_name}").stdout == ["human"]


def test_empty_paste_retains_the_core_floor(mcp_server: Server, mcp_pane: Pane) -> None:
    """Empty paste has no buffer effect on any supported release."""
    assert "pasted" in paste_text(
        "", pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name
    )
    assert not mcp_server.cmd("list-buffers", "-F", "#{buffer_name}").stdout


def test_load_buffer_stages_only_where_it_can_be_deleted(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A 3.3a daemon creates no buffer; 3.4 stages one the caller can delete."""
    mcp_server.set_buffer("retained", buffer_name="human")
    if not support.is_at_least(mcp_server._get_version(), "3.4"):
        with pytest.raises(ToolError, match=r"3\.4"):
            load_buffer("never-staged", socket_name=mcp_server.socket_name)
        assert mcp_server.cmd("list-buffers", "-F", "#{buffer_name}").stdout == [
            "human"
        ]
        return
    ref = load_buffer("baseline-retained", socket_name=mcp_server.socket_name)
    assert (
        show_buffer(ref.buffer_name, socket_name=mcp_server.socket_name).content
        == "baseline-retained"
    )
    delete_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)
    assert mcp_server.cmd("list-buffers", "-F", "#{buffer_name}").stdout == ["human"]


def test_owned_layout_dispatch_refuses_only_the_older_unsafe_error_path(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Raw helper validation also covers the 3.3a layout error boundary."""
    window_id = mcp_pane.window.window_id
    assert window_id is not None
    argv = _tmux_argv(mcp_server, "select-layout", "-t", window_id, "not-a-layout")
    if not support.is_at_least(mcp_server._get_version(), "3.4"):
        with pytest.raises(ValueError, match="layout"):
            _tmux_proc._run_tmux_sync_bounded(argv, timeout=5)
    else:
        assert (
            _tmux_proc._run_tmux_sync_bounded(argv, timeout=5, check=False).returncode
            != 0
        )
    assert mcp_server.is_alive()


def test_layout_tool_reports_invalid_layout_without_ending_the_daemon(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """The advertised layout tool carries the parent validation boundary."""
    with pytest.raises(ToolError, match="layout"):
        select_layout(
            "not-a-layout",
            window_id=mcp_pane.window.window_id,
            socket_name=mcp_server.socket_name,
        )
    assert mcp_server.is_alive()


@pytest.mark.parametrize(
    "command",
    [
        ["new-window", "-n", "a.b"],
        ["rename-window", "a:b"],
        ["new-session", "-s", "a.b"],
    ],
)
def test_owned_helpers_inherit_the_exact_37_name_guard(
    monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    """The owned fence runs libtmux's command policy, names included."""
    with pytest.raises(exc.VersionTooLow, match="invalid window or session name"):
        _tmux_proc._fence(["tmux", *command], "3.7", live=True)
    _tmux_proc._fence(["tmux", *command], "3.7a", live=True)
