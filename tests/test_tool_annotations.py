"""Tests for standard MCP hints and project-owned toolsets."""

from __future__ import annotations

import typing as t

import pytest

from libtmux_mcp._utils import (
    TOOLSET_EXECUTE,
    TOOLSET_INSPECT,
    TOOLSET_MANAGE,
    TOOLSET_TEARDOWN,
    VALID_TOOLSETS,
)
from libtmux_mcp.tools import register_tools

from .conftest import wire_annotations

CONSERVATIVE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}

EXPECTED_TOOLS_BY_TOOLSET = {
    TOOLSET_INSPECT: frozenset(
        {
            "call_read_tools_batch",
            "capture_pane",
            "capture_since",
            "display_message",
            "find_pane_by_position",
            "get_pane_info",
            "get_server_info",
            "get_session_info",
            "get_window_info",
            "list_panes",
            "list_servers",
            "list_sessions",
            "list_windows",
            "search_panes",
            "show_buffer",
            "show_environment",
            "show_hook",
            "show_hooks",
            "show_option",
            "snapshot_pane",
            "wait_for_text",
        }
    ),
    TOOLSET_MANAGE: frozenset(
        {
            "enter_copy_mode",
            "exit_copy_mode",
            "load_buffer",
            "move_window",
            "rename_session",
            "rename_window",
            "resize_pane",
            "resize_window",
            "select_layout",
            "select_pane",
            "select_window",
            "set_pane_title",
            "signal_channel",
            "swap_pane",
            "wait_for_channel",
        }
    ),
    TOOLSET_EXECUTE: frozenset(
        {
            "create_session",
            "create_window",
            "paste_buffer",
            "paste_text",
            "pipe_pane",
            "respawn_pane",
            "run_command",
            "send_keys",
            "send_keys_batch",
            "set_environment",
            "set_option",
            "split_window",
        }
    ),
    TOOLSET_TEARDOWN: frozenset(
        {
            "clear_pane",
            "delete_buffer",
            "kill_pane",
            "kill_server",
            "kill_session",
            "kill_window",
        }
    ),
}


@pytest.fixture(scope="module")
def advertised_tools() -> dict[str, t.Any]:
    """Return every registered tool keyed by name, as clients see it.

    Registers into a fresh server rather than the production one, whose
    toolset filter is fixed at import and may hide tools.
    """
    import asyncio

    from fastmcp import Client, FastMCP

    mcp = FastMCP(name="test-tool-annotations")
    register_tools(mcp)

    async def _list_tools() -> list[t.Any]:
        async with Client(mcp) as client:
            return list(await client.list_tools())

    tools = asyncio.run(_list_tools())
    return {tool.name: tool for tool in tools}


def _wire_tags(tool: t.Any) -> set[str]:
    dumped = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return set(dumped.get("_meta", {}).get("fastmcp", {}).get("tags", []))


def test_every_tool_advertises_conservative_mcp_hints(
    advertised_tools: dict[str, t.Any],
) -> None:
    """No static hint promises how a programmable tmux target will behave."""
    for name, tool in advertised_tools.items():
        assert wire_annotations(tool) == CONSERVATIVE_ANNOTATIONS, name


def test_every_tool_keeps_one_project_owned_toolset(
    advertised_tools: dict[str, t.Any],
) -> None:
    """The direct-operation taxonomy remains visible on the MCP wire."""
    known = set(VALID_TOOLSETS)
    for name, tool in advertised_tools.items():
        assert len(_wire_tags(tool) & known) == 1, name


@pytest.mark.parametrize(
    ("toolset", "expected"),
    EXPECTED_TOOLS_BY_TOOLSET.items(),
    ids=EXPECTED_TOOLS_BY_TOOLSET,
)
def test_toolset_memberships_match_direct_operations(
    advertised_tools: dict[str, t.Any],
    toolset: str,
    expected: frozenset[str],
) -> None:
    """Each toolset keeps its reviewed direct-operation inventory."""
    actual = {
        name for name, tool in advertised_tools.items() if toolset in _wire_tags(tool)
    }
    assert actual == expected
