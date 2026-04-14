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
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    ANNOTATIONS_SHELL,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
)
from libtmux_mcp.tools.pane_tools.copy_mode import enter_copy_mode, exit_copy_mode
from libtmux_mcp.tools.pane_tools.io import (
    capture_pane,
    clear_pane,
    paste_text,
    send_keys,
)
from libtmux_mcp.tools.pane_tools.layout import (
    resize_pane,
    select_pane,
    swap_pane,
)
from libtmux_mcp.tools.pane_tools.lifecycle import (
    get_pane_info,
    kill_pane,
    set_pane_title,
)
from libtmux_mcp.tools.pane_tools.meta import display_message, snapshot_pane
from libtmux_mcp.tools.pane_tools.pipe import pipe_pane
from libtmux_mcp.tools.pane_tools.search import search_panes
from libtmux_mcp.tools.pane_tools.wait import (
    wait_for_content_change,
    wait_for_text,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = [
    "capture_pane",
    "clear_pane",
    "display_message",
    "enter_copy_mode",
    "exit_copy_mode",
    "get_pane_info",
    "kill_pane",
    "paste_text",
    "pipe_pane",
    "register",
    "resize_pane",
    "search_panes",
    "select_pane",
    "send_keys",
    "set_pane_title",
    "snapshot_pane",
    "swap_pane",
    "wait_for_content_change",
    "wait_for_text",
]


def register(mcp: FastMCP) -> None:
    """Register pane-level tools with the MCP instance."""
    mcp.tool(title="Send Keys", annotations=ANNOTATIONS_SHELL, tags={TAG_MUTATING})(
        send_keys
    )
    mcp.tool(title="Capture Pane", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        capture_pane
    )
    mcp.tool(
        title="Resize Pane", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(resize_pane)
    mcp.tool(
        title="Kill Pane",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(kill_pane)
    mcp.tool(
        title="Set Pane Title", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(set_pane_title)
    mcp.tool(title="Get Pane Info", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        get_pane_info
    )
    mcp.tool(title="Clear Pane", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING})(
        clear_pane
    )
    mcp.tool(title="Search Panes", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        search_panes
    )
    mcp.tool(title="Wait For Text", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        wait_for_text
    )
    mcp.tool(title="Snapshot Pane", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        snapshot_pane
    )
    mcp.tool(
        title="Wait For Content Change",
        annotations=ANNOTATIONS_RO,
        tags={TAG_READONLY},
    )(wait_for_content_change)
    mcp.tool(
        title="Select Pane", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(select_pane)
    mcp.tool(title="Swap Pane", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING})(
        swap_pane
    )
    mcp.tool(title="Pipe Pane", annotations=ANNOTATIONS_SHELL, tags={TAG_MUTATING})(
        pipe_pane
    )
    mcp.tool(title="Display Message", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        display_message
    )
    mcp.tool(
        title="Enter Copy Mode",
        annotations=ANNOTATIONS_CREATE,
        tags={TAG_MUTATING},
    )(enter_copy_mode)
    mcp.tool(
        title="Exit Copy Mode",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(exit_copy_mode)
    mcp.tool(title="Paste Text", annotations=ANNOTATIONS_SHELL, tags={TAG_MUTATING})(
        paste_text
    )
