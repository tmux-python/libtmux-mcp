"""Generic MCP tool batching helpers."""

from __future__ import annotations

import json
import time
import typing as t

from fastmcp import Context
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel

from libtmux_mcp._utils import (
    ANNOTATIONS_AMBIENT_UNKNOWN,
    TAG_SELF_BOUNDED,
    TOOLSET_INSPECT,
    ExpectedToolError,
    handle_tool_errors_async,
)
from libtmux_mcp.middleware import DEFAULT_RESPONSE_LIMIT_BYTES
from libtmux_mcp.models import (
    ToolCallBatchResult,
    ToolCallOperation,
    ToolCallOperationResult,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

_OnError: t.TypeAlias = t.Literal["stop", "continue"]

_BATCH_TOOL_NAMES: frozenset[str] = frozenset({"call_read_tools_batch"})

MAX_BATCH_OPERATIONS = 1_000

_BATCH_TRUNCATED_CONTENT: list[dict[str, t.Any]] = [
    {
        "type": "text",
        "text": "[... batch truncated nested content ...]",
    }
]


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


async def _check_operation_allowed(
    *,
    fastmcp: FastMCP,
    operation: ToolCallOperation,
) -> None:
    """Validate that one nested operation targets an ``inspect`` tool."""
    if operation.tool in _BATCH_TOOL_NAMES:
        msg = "Batch tools cannot call batch tools recursively."
        raise ExpectedToolError(msg)

    tool = await fastmcp.get_tool(operation.tool)
    if tool is None:
        msg = f"Unknown tool: {operation.tool!r}"
        raise ExpectedToolError(msg)

    # The batch loop is serial with no aggregate deadline and
    # ``MAX_BATCH_OPERATIONS`` is 1000, so a self-bounded wait batched N
    # times costs N x its ceiling. Reject per-operation, not pre-loop, so
    # the raise becomes a ``success=False`` row and ``on_error='continue'``
    # isolation is preserved.
    if TAG_SELF_BOUNDED in tool.tags:
        msg = (
            f"Tool {operation.tool!r} enforces its own wait ceiling and "
            "cannot be batched; batching would multiply that ceiling by "
            "the operation count. Call it directly."
        )
        raise ExpectedToolError(msg)

    if TOOLSET_INSPECT not in tool.tags:
        msg = (
            f"Tool {operation.tool!r} is not an 'inspect' tool, so it "
            "cannot run in a read batch. Call it directly."
        )
        raise ExpectedToolError(msg)


def _ensure_tool_result(tool_name: str, result: t.Any) -> ToolResult:
    """Return ``result`` as a ``ToolResult`` or raise a row-level error."""
    if isinstance(result, ToolResult):
        return result
    msg = f"Tool {tool_name!r} returned an unsupported result."
    raise ExpectedToolError(msg)


def _batch_response_size(result: ToolCallBatchResult) -> int:
    """Return the serialized byte size of FastMCP's batch response envelope."""
    result_json = result.model_dump_json(fallback=str)
    envelope = {
        "content": [{"type": "text", "text": result_json}],
        "structuredContent": json.loads(result_json),
        "isError": False,
    }
    serialized = json.dumps(envelope, separators=(",", ":"), sort_keys=True)
    return len(serialized.encode("utf-8"))


def _operation_has_nested_payload(result: ToolCallOperationResult) -> bool:
    """Return True when a row still carries payload fields that can be elided."""
    return bool(result.content) or result.structured_content is not None


def _limit_batch_result(
    result: ToolCallBatchResult,
    *,
    max_bytes: int = DEFAULT_RESPONSE_LIMIT_BYTES,
) -> ToolCallBatchResult:
    """Elide nested result payloads until the batch envelope fits."""
    if _batch_response_size(result) <= max_bytes:
        return result

    limited = result.model_copy(
        deep=True,
        update={"response_truncated": True},
    )
    dropped_bytes = 0
    for operation in limited.results:
        if not _operation_has_nested_payload(operation):
            continue

        before = _batch_response_size(limited)
        operation.content = [item.copy() for item in _BATCH_TRUNCATED_CONTENT]
        operation.structured_content = None
        after = _batch_response_size(limited)
        dropped_bytes += max(before - after, 0)
        limited.response_truncated_bytes = dropped_bytes

        if _batch_response_size(limited) <= max_bytes:
            break

    return limited


async def _call_one_tool(
    *,
    fastmcp: FastMCP,
    operation: ToolCallOperation,
    index: int,
) -> ToolCallOperationResult:
    """Call one nested tool and convert its outcome to a batch result row."""
    start = time.monotonic()
    try:
        await _check_operation_allowed(
            fastmcp=fastmcp,
            operation=operation,
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
    ctx: Context | None,
) -> ToolCallBatchResult:
    """Execute nested MCP tool calls serially through FastMCP."""
    if not operations:
        msg = "operations must contain at least one tool call"
        raise ExpectedToolError(msg)
    if len(operations) > MAX_BATCH_OPERATIONS:
        msg = f"operations must contain at most {MAX_BATCH_OPERATIONS} tool calls"
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
        )
        results.append(result)
        if not result.success and on_error == "stop":
            stopped_at = index
            break

    succeeded = sum(1 for result in results if result.success)
    failed = len(results) - succeeded
    return _limit_batch_result(
        ToolCallBatchResult(
            results=results,
            succeeded=succeeded,
            failed=failed,
            stopped_at=stopped_at,
        )
    )


@handle_tool_errors_async
async def call_read_tools_batch(
    operations: list[ToolCallOperation],
    on_error: _OnError = "stop",
    ctx: Context | None = None,
) -> ToolCallBatchResult:
    """Call several `inspect` tools serially and return per-tool results.

    Use when one agent turn needs several observations. Each nested call
    still goes through FastMCP validation and this server's middleware.
    Only `inspect` tools are accepted; anything that changes tmux state
    is refused, whatever this server has enabled.

    This wrapper aggregates authority under its own name: a client rule
    keyed on a nested tool's name does not fire for a call made through
    it. Read it as authority to invoke any `inspect` tool.
    """
    return await _call_tools_batch(
        operations=operations,
        on_error=on_error,
        ctx=ctx,
    )


def register(mcp: FastMCP) -> None:
    """Register generic MCP batch tools."""
    mcp.tool(
        title="Call Read Tools Batch",
        annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
        tags={TOOLSET_INSPECT},
    )(call_read_tools_batch)
