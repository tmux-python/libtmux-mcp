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


def _batch_probe_server() -> FastMCP:
    """Build a small FastMCP server with batch tools and tiered probes."""
    from fastmcp import FastMCP

    from libtmux_mcp.middleware import SafetyMiddleware, ToolErrorResultMiddleware
    from libtmux_mcp.tools.batch_tools import register as register_batch_tools

    mcp = FastMCP(
        name="batch-probe",
        middleware=[
            ToolErrorResultMiddleware(transform_errors=True),
            SafetyMiddleware(max_tier=TAG_DESTRUCTIVE),
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


def test_call_readonly_tools_batch_preserves_structured_results() -> None:
    """The readonly batch wrapper returns per-tool structured content."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_readonly_tools_batch",
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
def test_call_readonly_tools_batch_caps_aggregate_response(
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
                "call_readonly_tools_batch",
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

    serialized = json.dumps(structured, separators=(",", ":"), sort_keys=True)
    assert len(serialized.encode("utf-8")) <= DEFAULT_RESPONSE_LIMIT_BYTES
    assert first_payload not in serialized
    assert second_payload in serialized

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
    assert second["structured_content"] == {"value": second_payload}


def test_call_readonly_tools_batch_rejects_mutating_inner_tool() -> None:
    """Readonly batching does not tunnel a mutating tool call."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_readonly_tools_batch",
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
    assert result.structured_content["succeeded"] == 0
    assert result.structured_content["failed"] == 1
    assert result.structured_content["stopped_at"] == 0
    [operation] = result.structured_content["results"]
    assert operation["success"] is False
    assert "exceeds batch tier readonly" in operation["error"]


def test_call_mutating_tools_batch_rejects_destructive_inner_tool() -> None:
    """Mutating batching does not tunnel a destructive tool call."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_mutating_tools_batch",
                {
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


def test_call_mutating_tools_batch_continues_after_error() -> None:
    """Continue mode attempts later operations after a failed tool call."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_mutating_tools_batch",
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
    """Batch wrappers cannot recursively call batch wrappers."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_destructive_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "call_destructive_tools_batch",
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
