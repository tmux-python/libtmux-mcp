"""Generic MCP tool batching helpers."""

from __future__ import annotations

import json
import time
import typing as t

from fastmcp import Context
from fastmcp.tools.base import ToolResult
from pydantic import BaseModel

from libtmux_mcp._utils import (
    ANNOTATIONS_RO,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    TAG_SELF_BOUNDED,
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

MAX_BATCH_OPERATIONS = 1_000

_BATCH_TRUNCATED_CONTENT: list[dict[str, t.Any]] = [
    {
        "type": "text",
        "text": "[... batch truncated nested content ...]",
    }
]

_ANNOTATIONS_BATCH_SIDE_EFFECTS: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


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
        # None means nonexistent OR disabled by tier, so raising
        # "Unknown tool" here denied that a gated tool exists. Hand it
        # on instead: the nested call runs with ``run_middleware=True``,
        # letting ``SafetyMiddleware`` name the tier and FastMCP still
        # raise ``NotFoundError`` for a typo. Nothing is skipped --
        # visibility follows tier tags, so an invisible tool is
        # off-tier by construction and is denied before these checks.
        return

    # ``max_tier`` is a CEILING, so a readonly tool is reachable through
    # every batch wrapper, not only the readonly one. The batch loop is
    # serial with no aggregate deadline and ``MAX_BATCH_OPERATIONS`` is
    # 1000, so a self-bounded wait batched N times costs N x its
    # ceiling. Reject per-operation (not pre-loop) so the raise becomes
    # a ``success=False`` row and ``on_error='continue'`` isolation is
    # preserved.
    if TAG_SELF_BOUNDED in tool.tags:
        msg = (
            f"Tool {operation.tool!r} enforces its own wait ceiling and "
            "cannot be batched; batching would multiply that ceiling by "
            "the operation count. Call it directly."
        )
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
    timeout: float | None = None,
) -> ToolCallBatchResult:
    """Execute nested MCP tool calls serially through FastMCP."""
    if timeout is not None and timeout <= 0:
        msg = f"timeout must be positive, or null for no cap (received {timeout})"
        raise ExpectedToolError(msg)
    if not operations:
        msg = "operations must contain at least one tool call"
        raise ExpectedToolError(msg)
    if len(operations) > MAX_BATCH_OPERATIONS:
        msg = f"operations must contain at most {MAX_BATCH_OPERATIONS} tool calls"
        raise ExpectedToolError(msg)
    if on_error not in {"stop", "continue"}:
        msg = f"on_error must be 'stop' or 'continue' (received {on_error!r})"
        raise ExpectedToolError(msg)
    if ctx is None:
        msg = "FastMCP context is required; call this tool through MCP."
        raise ExpectedToolError(msg)

    results: list[ToolCallOperationResult] = []
    stopped_at: int | None = None
    deadline = time.monotonic() + timeout if timeout is not None else None
    for index, operation in enumerate(operations):
        # Checked BETWEEN operations, which is enough here and would not
        # have been for a caller-supplied regex: the time is genuinely
        # in this loop. A thousand operations is the cap and a thousand
        # mutations took 67 seconds, and a client that gives up does not
        # stop the server -- 617 further mutations landed after the
        # caller was gone, with no report of where it stopped.
        if deadline is not None and time.monotonic() > deadline:
            assert timeout is not None
            results.append(
                ToolCallOperationResult(
                    index=index,
                    tool=operation.tool,
                    success=False,
                    error=(
                        f"batch execution exceeded timeout of {timeout}s; "
                        "operations from this index onward did not run"
                    ),
                    elapsed_seconds=0.0,
                )
            )
            stopped_at = index
            break
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
    return _limit_batch_result(
        ToolCallBatchResult(
            results=results,
            succeeded=succeeded,
            failed=failed,
            stopped_at=stopped_at,
        )
    )


@handle_tool_errors_async
async def call_readonly_tools_batch(
    operations: list[ToolCallOperation],
    on_error: _OnError = "stop",
    timeout: float | None = None,
    ctx: Context | None = None,
) -> ToolCallBatchResult:
    """Call readonly MCP tools serially and return per-tool results.

    Use when several read-only observations should be made in one agent
    turn. Each nested call still goes through FastMCP validation,
    middleware, and safety checks. Mutating and destructive tools are
    rejected even if the server process itself is running at a higher
    safety tier.

    Batching saves the transport round trip and re-pays the per-call
    framework cost, so it is not a speed win below about three or four
    operations -- measured 2.5x SLOWER at one, 1.2x at two, break-even
    around three, and 0.80x at ten. It pays most when the nested
    operations are individually expensive: a mixed read of
    ``get_pane_info`` + ``list_panes`` + ``show_option`` + ``capture_pane``
    measured 65 ms batched against 120 ms serial.

    ``timeout`` bounds the WHOLE batch, checked between operations.
    Without it a batch runs to completion, and the cap is 1000 calls:
    a client that gives up does not stop the server, so the work keeps
    applying after the caller is gone.
    """
    return await _call_tools_batch(
        operations=operations,
        on_error=on_error,
        timeout=timeout,
        max_tier=TAG_READONLY,
        ctx=ctx,
    )


@handle_tool_errors_async
async def call_mutating_tools_batch(
    operations: list[ToolCallOperation],
    on_error: _OnError = "stop",
    timeout: float | None = None,
    ctx: Context | None = None,
) -> ToolCallBatchResult:
    """Call readonly or mutating MCP tools serially and return per-tool results.

    Use for ordered tmux workflows where every step is still an existing
    typed MCP tool. Destructive tools are rejected regardless of the
    process-wide safety tier.

    ``timeout`` bounds the WHOLE batch, checked between operations.
    Without it a batch runs to completion, and the cap is 1000 calls:
    a client that gives up does not stop the server, so the work keeps
    applying after the caller is gone.
    """
    return await _call_tools_batch(
        operations=operations,
        on_error=on_error,
        timeout=timeout,
        max_tier=TAG_MUTATING,
        ctx=ctx,
    )


@handle_tool_errors_async
async def call_destructive_tools_batch(
    operations: list[ToolCallOperation],
    on_error: _OnError = "stop",
    timeout: float | None = None,
    ctx: Context | None = None,
) -> ToolCallBatchResult:
    """Call readonly, mutating, or destructive MCP tools serially.

    This wrapper preserves the normal per-tool schemas and middleware
    but its tier permits destructive nested operations. Prefer the
    narrower readonly or mutating wrappers whenever possible.

    ``timeout`` bounds the WHOLE batch, checked between operations.
    Without it a batch runs to completion, and the cap is 1000 calls:
    a client that gives up does not stop the server, so the work keeps
    applying after the caller is gone.
    """
    return await _call_tools_batch(
        operations=operations,
        on_error=on_error,
        timeout=timeout,
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
        annotations=_ANNOTATIONS_BATCH_SIDE_EFFECTS,
        tags={TAG_MUTATING},
    )(call_mutating_tools_batch)
    mcp.tool(
        title="Call Destructive Tools Batch",
        annotations=_ANNOTATIONS_BATCH_SIDE_EFFECTS,
        tags={TAG_DESTRUCTIVE},
    )(call_destructive_tools_batch)
