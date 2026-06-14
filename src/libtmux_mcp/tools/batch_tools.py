"""Generic MCP tool batching helpers."""

from __future__ import annotations

import time
import typing as t

from fastmcp import Context
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel

from libtmux_mcp._utils import (
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_RO,
    ANNOTATIONS_SHELL,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    ExpectedToolError,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    ToolCallBatchResult,
    ToolCallOperation,
    ToolCallOperationResult,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

_OnError: t.TypeAlias = t.Literal["stop", "continue"]

_TIER_LEVELS: dict[str, int] = {
    TAG_READONLY: 0,
    TAG_MUTATING: 1,
    TAG_DESTRUCTIVE: 2,
}

_BATCH_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "call_readonly_tools_batch",
        "call_mutating_tools_batch",
        "call_destructive_tools_batch",
    }
)


def _content_block_to_dict(block: t.Any) -> dict[str, t.Any]:
    """Return a JSON-ready representation of an MCP content block."""
    if isinstance(block, BaseModel):
        return block.model_dump(mode="json", by_alias=True, exclude_none=True)
    if hasattr(block, "model_dump"):
        dumped = block.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(dumped, dict):
            return t.cast("dict[str, t.Any]", dumped)
    return {"type": type(block).__name__, "value": str(block)}


def _result_error_text(result: ToolResult) -> str | None:
    """Extract a readable error string from a FastMCP ``ToolResult``."""
    text_blocks: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_blocks.append(text)
    if text_blocks:
        return "\n".join(text_blocks)
    if result.is_error:
        return "Tool call returned an error result."
    return None


def _tool_tier(tool_name: str, tags: set[str]) -> str:
    """Return the highest recognized safety tier for a registered tool."""
    found = [tier for tier in _TIER_LEVELS if tier in tags]
    if not found:
        msg = f"Tool {tool_name!r} has no recognized safety tier tag."
        raise ExpectedToolError(msg)
    return max(found, key=lambda tier: _TIER_LEVELS[tier])


def _check_operation_allowed(
    *,
    tool_name: str,
    tool_tier: str,
    max_tier: str,
) -> None:
    """Raise when a nested tool exceeds this batch wrapper's tier."""
    if _TIER_LEVELS[tool_tier] <= _TIER_LEVELS[max_tier]:
        return
    msg = (
        f"Tool {tool_name!r} has tier {tool_tier!r}, which exceeds "
        f"batch tier {max_tier}."
    )
    raise ExpectedToolError(msg)


async def _get_allowed_tool_tier(
    *,
    fastmcp: FastMCP,
    operation: ToolCallOperation,
    max_tier: str,
) -> None:
    """Validate that one nested operation targets an allowed tool."""
    if operation.tool in _BATCH_TOOL_NAMES:
        msg = "Batch tools cannot call batch tools recursively."
        raise ExpectedToolError(msg)

    tool = await fastmcp.get_tool(operation.tool)
    if tool is None:
        msg = f"Unknown tool: {operation.tool!r}"
        raise ExpectedToolError(msg)

    tool_tier = _tool_tier(operation.tool, tool.tags)
    _check_operation_allowed(
        tool_name=operation.tool,
        tool_tier=tool_tier,
        max_tier=max_tier,
    )


def _ensure_tool_result(tool_name: str, result: t.Any) -> ToolResult:
    """Return ``result`` as a ``ToolResult`` or raise a row-level error."""
    if isinstance(result, ToolResult):
        return result
    msg = f"Tool {tool_name!r} returned an unsupported result."
    raise ExpectedToolError(msg)


async def _call_one_tool(
    *,
    fastmcp: FastMCP,
    operation: ToolCallOperation,
    index: int,
    max_tier: str,
) -> ToolCallOperationResult:
    """Call one nested tool and convert its outcome to a batch result row."""
    start = time.monotonic()
    try:
        await _get_allowed_tool_tier(
            fastmcp=fastmcp,
            operation=operation,
            max_tier=max_tier,
        )

        result = _ensure_tool_result(
            operation.tool,
            await fastmcp.call_tool(
                operation.tool,
                operation.arguments,
                run_middleware=True,
            ),
        )

        error = _result_error_text(result)
        return ToolCallOperationResult(
            index=index,
            tool=operation.tool,
            success=not result.is_error,
            error=error if result.is_error else None,
            content=[_content_block_to_dict(block) for block in result.content],
            structured_content=result.structured_content,
            meta=result.meta,
            elapsed_seconds=time.monotonic() - start,
        )
    except Exception as exc:
        return ToolCallOperationResult(
            index=index,
            tool=operation.tool,
            success=False,
            error=str(exc),
            elapsed_seconds=time.monotonic() - start,
        )


async def _call_tools_batch(
    *,
    operations: list[ToolCallOperation],
    on_error: _OnError,
    max_tier: str,
    ctx: Context | None,
) -> ToolCallBatchResult:
    """Execute nested MCP tool calls serially through FastMCP."""
    if not operations:
        msg = "operations must contain at least one tool call"
        raise ExpectedToolError(msg)
    if on_error not in {"stop", "continue"}:
        msg = "on_error must be 'stop' or 'continue'"
        raise ExpectedToolError(msg)
    if ctx is None:
        msg = "FastMCP context is required; call this tool through MCP."
        raise ExpectedToolError(msg)

    results: list[ToolCallOperationResult] = []
    stopped_at: int | None = None
    for index, operation in enumerate(operations):
        result = await _call_one_tool(
            fastmcp=ctx.fastmcp,
            operation=operation,
            index=index,
            max_tier=max_tier,
        )
        results.append(result)
        if not result.success and on_error == "stop":
            stopped_at = index
            break

    succeeded = sum(1 for result in results if result.success)
    failed = len(results) - succeeded
    return ToolCallBatchResult(
        results=results,
        succeeded=succeeded,
        failed=failed,
        stopped_at=stopped_at,
    )


@handle_tool_errors_async
async def call_readonly_tools_batch(
    operations: list[ToolCallOperation],
    on_error: _OnError = "stop",
    ctx: Context | None = None,
) -> ToolCallBatchResult:
    """Call readonly MCP tools serially and return per-tool results.

    Use when several read-only observations should be made in one agent
    turn. Each nested call still goes through FastMCP validation,
    middleware, and safety checks. Mutating and destructive tools are
    rejected even if the server process itself is running at a higher
    safety tier.
    """
    return await _call_tools_batch(
        operations=operations,
        on_error=on_error,
        max_tier=TAG_READONLY,
        ctx=ctx,
    )


@handle_tool_errors_async
async def call_mutating_tools_batch(
    operations: list[ToolCallOperation],
    on_error: _OnError = "stop",
    ctx: Context | None = None,
) -> ToolCallBatchResult:
    """Call readonly or mutating MCP tools serially and return per-tool results.

    Use for ordered tmux workflows where every step is still an existing
    typed MCP tool. Destructive tools are rejected regardless of the
    process-wide safety tier.
    """
    return await _call_tools_batch(
        operations=operations,
        on_error=on_error,
        max_tier=TAG_MUTATING,
        ctx=ctx,
    )


@handle_tool_errors_async
async def call_destructive_tools_batch(
    operations: list[ToolCallOperation],
    on_error: _OnError = "stop",
    ctx: Context | None = None,
) -> ToolCallBatchResult:
    """Call readonly, mutating, or destructive MCP tools serially.

    This wrapper preserves the normal per-tool schemas and middleware
    but its tier permits destructive nested operations. Prefer the
    narrower readonly or mutating wrappers whenever possible.
    """
    return await _call_tools_batch(
        operations=operations,
        on_error=on_error,
        max_tier=TAG_DESTRUCTIVE,
        ctx=ctx,
    )


def register(mcp: FastMCP) -> None:
    """Register generic MCP batch tools."""
    mcp.tool(
        title="Call Readonly Tools Batch",
        annotations=ANNOTATIONS_RO,
        tags={TAG_READONLY},
    )(call_readonly_tools_batch)
    mcp.tool(
        title="Call Mutating Tools Batch",
        annotations=ANNOTATIONS_SHELL,
        tags={TAG_MUTATING},
    )(call_mutating_tools_batch)
    mcp.tool(
        title="Call Destructive Tools Batch",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(call_destructive_tools_batch)
