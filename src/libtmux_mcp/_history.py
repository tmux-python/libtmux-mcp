"""Semantic shell-history policy helpers."""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


def _resolve_suppress_history(value: str | None) -> bool:
    """Resolve the strict startup history-suppression setting."""
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    msg = "LIBTMUX_SUPPRESS_HISTORY must be unset, '0', or '1'"
    raise ValueError(msg)


def _configure_history_defaults(
    mcp: FastMCP,
    enabled: bool,
    *,
    tool_names: tuple[str, ...] = ("run_command",),
) -> None:
    """Publish the effective default for semantic MCP command tools."""
    from fastmcp.server.transforms import ToolTransform
    from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig

    argument = ArgTransformConfig(default=enabled)
    mcp.add_transform(
        ToolTransform(
            {
                name: ToolTransformConfig(arguments={"suppress_history": argument})
                for name in tool_names
            }
        )
    )
