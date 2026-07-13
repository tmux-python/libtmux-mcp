"""Tests for generic MCP tool batching."""

from __future__ import annotations

import asyncio
import json
import typing as t

import pytest

from libtmux_mcp._utils import (
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


class BatchResponseLimitFixture(t.NamedTuple):
    """Test fixture for aggregate batch response limiting."""

    test_id: str
    payload_size: int


BATCH_RESPONSE_LIMIT_FIXTURES: list[BatchResponseLimitFixture] = [
    BatchResponseLimitFixture(
        test_id="two_large_readonly_results",
        payload_size=300_000,
    ),
]


class BatchOperationLimitFixture(t.NamedTuple):
    """Test fixture for operation-count batch limiting."""

    test_id: str
    operation_count: int


BATCH_OPERATION_LIMIT_FIXTURES: list[BatchOperationLimitFixture] = [
    BatchOperationLimitFixture(
        test_id="many_missing_tools",
        operation_count=6_000,
    ),
]


def _content_block_to_wire(block: t.Any) -> dict[str, t.Any]:
    if hasattr(block, "model_dump"):
        dumped = block.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(dumped, dict):
            return t.cast("dict[str, t.Any]", dumped)
    return {"type": type(block).__name__, "value": str(block)}


def _call_tool_result_wire(result: t.Any) -> dict[str, t.Any]:
    return {
        "content": [_content_block_to_wire(block) for block in result.content],
        "structuredContent": result.structured_content,
        "isError": result.is_error,
    }


def _batch_probe_server(server_tier: str = TAG_DESTRUCTIVE) -> FastMCP:
    """Build a small FastMCP server with the batch tool and tiered probes."""
    from fastmcp import FastMCP

    from libtmux_mcp.middleware import SafetyMiddleware, ToolErrorResultMiddleware
    from libtmux_mcp.tools.batch_tools import register as register_batch_tools

    mcp = FastMCP(
        name="batch-probe",
        middleware=[
            ToolErrorResultMiddleware(transform_errors=True),
            SafetyMiddleware(max_tier=server_tier),
        ],
    )
    register_batch_tools(mcp)

    @mcp.tool(title="Readonly Probe", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})
    def readonly_probe(value: str) -> dict[str, str]:
        return {"value": value}

    @mcp.tool(
        title="Mutating Probe",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )
    def mutating_probe(value: str) -> dict[str, str]:
        return {"value": value}

    @mcp.tool(
        title="Destructive Probe",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )
    def destructive_probe(value: str) -> dict[str, str]:
        return {"value": value}

    return mcp


def test_call_tools_batch_preserves_structured_results() -> None:
    """The batch tool returns per-tool structured content."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "readonly_probe",
                            "arguments": {"value": "alpha"},
                        },
                        {
                            "tool": "readonly_probe",
                            "arguments": {"value": "beta"},
                        },
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is False
    assert result.structured_content["succeeded"] == 2
    assert result.structured_content["failed"] == 0
    assert result.structured_content["stopped_at"] is None
    first, second = result.structured_content["results"]
    assert first == {
        "index": 0,
        "tool": "readonly_probe",
        "success": True,
        "error": None,
        "content": [{"type": "text", "text": '{"value":"alpha"}'}],
        "structured_content": {"value": "alpha"},
        "meta": None,
        "elapsed_seconds": first["elapsed_seconds"],
    }
    assert second == {
        "index": 1,
        "tool": "readonly_probe",
        "success": True,
        "error": None,
        "content": [{"type": "text", "text": '{"value":"beta"}'}],
        "structured_content": {"value": "beta"},
        "meta": None,
        "elapsed_seconds": second["elapsed_seconds"],
    }
    assert first["elapsed_seconds"] >= 0.0
    assert second["elapsed_seconds"] >= 0.0


@pytest.mark.parametrize(
    BatchResponseLimitFixture._fields,
    BATCH_RESPONSE_LIMIT_FIXTURES,
    ids=[fixture.test_id for fixture in BATCH_RESPONSE_LIMIT_FIXTURES],
)
def test_call_tools_batch_caps_aggregate_response(
    test_id: str,
    payload_size: int,
) -> None:
    """The batch envelope survives when nested result payloads are capped."""
    from fastmcp import Client

    from libtmux_mcp.middleware import DEFAULT_RESPONSE_LIMIT_BYTES

    first_payload = "first-" + ("a" * payload_size)
    second_payload = "second-" + ("b" * payload_size)

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "readonly_probe",
                            "arguments": {"value": first_payload},
                        },
                        {
                            "tool": "readonly_probe",
                            "arguments": {"value": second_payload},
                        },
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is False
    structured = result.structured_content
    assert structured["response_truncated"] is True
    assert structured["response_truncated_bytes"] > 0
    assert structured["succeeded"] == 2
    assert structured["failed"] == 0
    assert structured["stopped_at"] is None

    serialized = json.dumps(
        _call_tool_result_wire(result),
        separators=(",", ":"),
        sort_keys=True,
    )
    assert len(serialized.encode("utf-8")) <= DEFAULT_RESPONSE_LIMIT_BYTES
    assert first_payload not in serialized
    assert second_payload not in serialized

    first, second = structured["results"]
    assert first["index"] == 0
    assert first["tool"] == "readonly_probe"
    assert first["success"] is True
    assert first["structured_content"] is None
    assert first["content"] == [
        {
            "type": "text",
            "text": "[... batch truncated nested content ...]",
        }
    ]
    assert second["structured_content"] is None
    assert second["content"] == [
        {
            "type": "text",
            "text": "[... batch truncated nested content ...]",
        }
    ]


@pytest.mark.parametrize(
    BatchOperationLimitFixture._fields,
    BATCH_OPERATION_LIMIT_FIXTURES,
    ids=[fixture.test_id for fixture in BATCH_OPERATION_LIMIT_FIXTURES],
)
def test_call_tools_batch_rejects_oversized_operation_count(
    test_id: str,
    operation_count: int,
) -> None:
    """The batch tool rejects requests whose rows alone can exceed the cap."""
    from fastmcp import Client

    from libtmux_mcp.middleware import DEFAULT_RESPONSE_LIMIT_BYTES

    assert test_id

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "missing_probe",
                            "arguments": {},
                        }
                        for _ in range(operation_count)
                    ],
                    "on_error": "continue",
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())
    serialized = json.dumps(
        _call_tool_result_wire(result),
        separators=(",", ":"),
        sort_keys=True,
    )

    assert len(serialized.encode("utf-8")) <= DEFAULT_RESPONSE_LIMIT_BYTES
    assert result.is_error is True
    assert result.structured_content is None
    assert "operations must contain at most" in serialized


def test_call_tools_batch_max_tier_readonly_rejects_mutating_inner_tool() -> None:
    """max_tier="readonly" refuses a mutating nested tool below the server tier."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "max_tier": "readonly",
                    "operations": [
                        {
                            "tool": "mutating_probe",
                            "arguments": {"value": "changed"},
                        }
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is False
    assert result.structured_content["succeeded"] == 0
    assert result.structured_content["failed"] == 1
    assert result.structured_content["stopped_at"] == 0
    [operation] = result.structured_content["results"]
    assert operation["success"] is False
    assert "exceeds batch tier readonly" in operation["error"]


def test_call_tools_batch_max_tier_mutating_rejects_destructive_inner_tool() -> None:
    """max_tier="mutating" refuses a destructive nested tool below the server tier."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "max_tier": "mutating",
                    "operations": [
                        {
                            "tool": "destructive_probe",
                            "arguments": {"value": "destroy"},
                        }
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is False
    [operation] = result.structured_content["results"]
    assert operation["success"] is False
    assert "exceeds batch tier mutating" in operation["error"]


def test_call_tools_batch_bounded_by_server_tier() -> None:
    """A readonly-tier server blocks a mutating nested tool even with no max_tier.

    The batch tool is registered readonly so it stays callable at every tier,
    but each nested call re-runs the safety middleware, so a readonly server
    still refuses a mutating nested tool the batch did not cap itself.
    """
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server(server_tier=TAG_READONLY)) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "mutating_probe",
                            "arguments": {"value": "changed"},
                        }
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is False
    [operation] = result.structured_content["results"]
    assert operation["success"] is False


def test_call_tools_batch_continues_after_error() -> None:
    """Continue mode attempts later operations after a failed tool call."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "on_error": "continue",
                    "operations": [
                        {
                            "tool": "missing_probe",
                            "arguments": {},
                        },
                        {
                            "tool": "mutating_probe",
                            "arguments": {"value": "kept-going"},
                        },
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is False
    assert result.structured_content["succeeded"] == 1
    assert result.structured_content["failed"] == 1
    assert result.structured_content["stopped_at"] is None
    first, second = result.structured_content["results"]
    assert first["success"] is False
    assert second["success"] is True
    assert second["structured_content"] == {"value": "kept-going"}


def test_call_tools_batch_rejects_self_invocation() -> None:
    """The batch tool cannot recursively call the batch tool."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "call_tools_batch",
                            "arguments": {"operations": []},
                        }
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert result.is_error is False
    [operation] = result.structured_content["results"]
    assert operation["success"] is False
    assert "cannot call batch tools recursively" in operation["error"]


def test_call_tools_batch_advertises_worst_case_annotations() -> None:
    """The batch tool advertises possible side effects."""
    mcp = _batch_probe_server()

    tool = asyncio.run(mcp.get_tool("call_tools_batch"))
    assert tool is not None
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.idempotentHint is False
    assert tool.annotations.openWorldHint is True
