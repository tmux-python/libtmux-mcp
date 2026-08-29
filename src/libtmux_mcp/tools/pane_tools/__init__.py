"""Pane-level MCP tools, organised by domain.

The package is structured by operation kind (io, wait, search,
copy_mode, layout, lifecycle, pipe, meta). Consumers can continue to
import ``libtmux_mcp.tools.pane_tools`` — re-exports below preserve
the historical flat namespace so existing tests and typed imports
keep working.
"""

from __future__ import annotations

import typing as t

from libtmux_mcp._utils import (
    ANNOTATIONS_CHANGE,
    ANNOTATIONS_DELETE,
    ANNOTATIONS_OBSERVE,
    ANNOTATIONS_OBSERVE_CONTENT,
    ANNOTATIONS_PANE_INPUT,
    DISCOVERY_META,
    TAG_SELF_BOUNDED,
    TOOLSET_EXECUTE,
    TOOLSET_INSPECT,
    TOOLSET_MANAGE,
    TOOLSET_TEARDOWN,
)
from libtmux_mcp.tools.pane_tools.capture_since import capture_since
from libtmux_mcp.tools.pane_tools.copy_mode import enter_copy_mode, exit_copy_mode
from libtmux_mcp.tools.pane_tools.io import (
    capture_pane,
    clear_pane,
    paste_text,
    run_command,
    send_keys,
    send_keys_batch,
)
from libtmux_mcp.tools.pane_tools.layout import (
    resize_pane,
    select_pane,
    swap_pane,
)
from libtmux_mcp.tools.pane_tools.lifecycle import (
    find_pane_by_position,
    get_pane_info,
    kill_pane,
    respawn_pane,
    set_pane_title,
)
from libtmux_mcp.tools.pane_tools.meta import display_message, snapshot_pane
from libtmux_mcp.tools.pane_tools.pipe import pipe_pane
from libtmux_mcp.tools.pane_tools.search import search_panes
from libtmux_mcp.tools.pane_tools.wait import wait_for_text

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = [
    "capture_pane",
    "capture_since",
    "clear_pane",
    "display_message",
    "enter_copy_mode",
    "exit_copy_mode",
    "find_pane_by_position",
    "get_pane_info",
    "kill_pane",
    "paste_text",
    "pipe_pane",
    "register",
    "resize_pane",
    "respawn_pane",
    "run_command",
    "search_panes",
    "select_pane",
    "send_keys",
    "send_keys_batch",
    "set_pane_title",
    "snapshot_pane",
    "swap_pane",
    "wait_for_text",
]


def register(mcp: FastMCP) -> None:
    """Register pane-level tools with the MCP instance."""
    mcp.tool(
        title="Send Keys", annotations=ANNOTATIONS_PANE_INPUT, tags={TOOLSET_EXECUTE}
    )(send_keys)
    mcp.tool(
        title="Send Keys Batch",
        annotations=ANNOTATIONS_PANE_INPUT,
        tags={TOOLSET_EXECUTE},
    )(send_keys_batch)
    # run_command blocks on ``tmux wait-for`` under the same wait
    # ceiling as the wait tools, so TAG_SELF_BOUNDED excludes it from
    # the batch wrappers: a serial batch would multiply that ceiling by
    # the operation count. Use send_keys_batch for command sequences.
    mcp.tool(
        title="Run Command",
        annotations=ANNOTATIONS_PANE_INPUT,
        tags={TOOLSET_EXECUTE, TAG_SELF_BOUNDED},
    )(run_command)
    mcp.tool(
        title="Capture Pane",
        annotations=ANNOTATIONS_OBSERVE_CONTENT,
        tags={TOOLSET_INSPECT},
    )(capture_pane)
    mcp.tool(
        title="Capture Since",
        annotations=ANNOTATIONS_OBSERVE_CONTENT,
        tags={TOOLSET_INSPECT},
    )(capture_since)
    mcp.tool(
        title="Resize Pane", annotations=ANNOTATIONS_CHANGE, tags={TOOLSET_MANAGE}
    )(resize_pane)
    mcp.tool(
        title="Kill Pane",
        annotations=ANNOTATIONS_DELETE,
        tags={TOOLSET_TEARDOWN},
    )(kill_pane)
    mcp.tool(
        title="Respawn Pane",
        annotations=ANNOTATIONS_PANE_INPUT,
        tags={TOOLSET_EXECUTE},
    )(respawn_pane)
    mcp.tool(
        title="Set Pane Title", annotations=ANNOTATIONS_CHANGE, tags={TOOLSET_MANAGE}
    )(set_pane_title)
    mcp.tool(
        title="Get Pane Info", annotations=ANNOTATIONS_OBSERVE, tags={TOOLSET_INSPECT}
    )(get_pane_info)
    mcp.tool(
        title="Find Pane By Position",
        annotations=ANNOTATIONS_OBSERVE,
        tags={TOOLSET_INSPECT},
    )(find_pane_by_position)
    mcp.tool(
        title="Clear Pane",
        annotations=ANNOTATIONS_DELETE,
        tags={TOOLSET_TEARDOWN},
    )(clear_pane)
    mcp.tool(
        title="Search Panes",
        annotations=ANNOTATIONS_OBSERVE_CONTENT,
        tags={TOOLSET_INSPECT},
    )(search_panes)
    # TAG_SELF_BOUNDED excludes this tool from retry and from batch
    # wrappers: both would multiply the wait ceiling it enforces.
    mcp.tool(
        title="Wait For Text",
        annotations=ANNOTATIONS_OBSERVE_CONTENT,
        tags={TOOLSET_INSPECT, TAG_SELF_BOUNDED},
    )(wait_for_text)
    mcp.tool(
        title="Snapshot Pane",
        annotations=ANNOTATIONS_OBSERVE_CONTENT,
        tags={TOOLSET_INSPECT},
        meta=DISCOVERY_META,
    )(snapshot_pane)
    mcp.tool(
        title="Select Pane", annotations=ANNOTATIONS_CHANGE, tags={TOOLSET_MANAGE}
    )(select_pane)
    mcp.tool(title="Swap Pane", annotations=ANNOTATIONS_DELETE, tags={TOOLSET_MANAGE})(
        swap_pane
    )
    mcp.tool(
        title="Pipe Pane", annotations=ANNOTATIONS_PANE_INPUT, tags={TOOLSET_EXECUTE}
    )(pipe_pane)
    mcp.tool(
        title="Evaluate tmux Format String",
        annotations=ANNOTATIONS_OBSERVE_CONTENT,
        tags={TOOLSET_INSPECT},
    )(display_message)
    mcp.tool(
        title="Enter Copy Mode",
        annotations=ANNOTATIONS_DELETE,
        tags={TOOLSET_MANAGE},
    )(enter_copy_mode)
    mcp.tool(
        title="Exit Copy Mode",
        annotations=ANNOTATIONS_CHANGE,
        tags={TOOLSET_MANAGE},
    )(exit_copy_mode)
    mcp.tool(
        title="Paste Text", annotations=ANNOTATIONS_PANE_INPUT, tags={TOOLSET_EXECUTE}
    )(paste_text)
