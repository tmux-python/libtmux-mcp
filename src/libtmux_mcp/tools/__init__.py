"""MCP tool registration for libtmux."""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


def register_tools(mcp: FastMCP) -> None:
    """Register all tool modules with the FastMCP instance."""
    from libtmux_mcp.tools import (
        batch_tools,
        buffer_tools,
        engine_plan,
        engine_watch,
        env_tools,
        hook_tools,
        option_tools,
        pane_tools,
        server_tools,
        session_tools,
        wait_for_tools,
        window_tools,
    )

    batch_tools.register(mcp)
    server_tools.register(mcp)
    session_tools.register(mcp)
    window_tools.register(mcp)
    pane_tools.register(mcp)
    option_tools.register(mcp)
    env_tools.register(mcp)
    wait_for_tools.register(mcp)
    buffer_tools.register(mcp)
    hook_tools.register(mcp)
    # Opt-in (LIBTMUX_MCP_ENGINE_OPS=1): chained plan tools + watchers over
    # engine-ops. engine_watch's engine is started/closed by the server lifespan.
    engine_plan.register(mcp)
    engine_watch.register(mcp)
