"""Narrow prompt surface for libtmux-mcp.

The canonical operator guide lives at :doc:`docs/topics/prompting`.
This package exposes a small set of those recipes as first-class MCP
prompts so clients can discover them through the protocol instead of
requiring users to copy paragraphs out of docs.

The set is intentionally narrow: libtmux-mcp is a terminal control
plane, not a reasoning-workflow catalog. Treat expansion as out of
scope.
"""

from __future__ import annotations

import os
import typing as t
from collections.abc import Sequence

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

from libtmux_mcp._utils import TOOLSET_INSPECT
from libtmux_mcp.prompts.recipes import (
    build_dev_workspace,
    diagnose_failing_pane,
    interrupt_gracefully,
    run_and_wait,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.tools import Tool

#: Env-var gate that enables exposing prompts as tools for clients that
#: do not speak the MCP prompts protocol. Off by default — a sprawling
#: prompt catalog is not the goal.
ENV_PROMPTS_AS_TOOLS = "LIBTMUX_MCP_PROMPTS_AS_TOOLS"

_PROMPT_TOOL_NAMES = frozenset({"list_prompts", "get_prompt"})
_PROMPT_TOOL_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

__all__ = ["ENV_PROMPTS_AS_TOOLS", "register_prompts"]


class _PromptToolMetadata(Transform):
    """Classify generated prompt adapters as server-local inspect tools."""

    @staticmethod
    def _decorate(tool: Tool) -> Tool:
        if tool.name not in _PROMPT_TOOL_NAMES:
            return tool
        return tool.model_copy(
            update={
                "tags": {*tool.tags, TOOLSET_INSPECT},
                "annotations": _PROMPT_TOOL_ANNOTATIONS,
            }
        )

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Decorate generated adapters in tool listings."""
        return [self._decorate(tool) for tool in tools]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        """Decorate a generated adapter fetched for a call."""
        tool = await call_next(name, version=version)
        return self._decorate(tool) if tool is not None else None


def register_prompts(mcp: FastMCP) -> None:
    """Register the narrow prompt set with the MCP instance.

    When ``LIBTMUX_MCP_PROMPTS_AS_TOOLS=1``, also install the
    ``PromptsAsTools`` transform so clients that only speak the MCP
    tools protocol (some CLI agents and older SDKs) can still reach
    the prompts as ``list_prompts`` / ``get_prompt`` tool calls.
    """
    mcp.prompt(run_and_wait)
    mcp.prompt(diagnose_failing_pane)
    mcp.prompt(build_dev_workspace)
    mcp.prompt(interrupt_gracefully)

    if os.environ.get(ENV_PROMPTS_AS_TOOLS) == "1":
        from fastmcp.server.transforms import PromptsAsTools

        mcp.add_transform(PromptsAsTools(mcp))
        mcp.add_transform(_PromptToolMetadata())
