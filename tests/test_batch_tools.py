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
    TAG_SELF_BOUNDED,
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


class BatchAnnotationFixture(t.NamedTuple):
    """Test fixture for generic batch wrapper annotations."""

    test_id: str
    tool_name: str
    read_only_hint: bool
    destructive_hint: bool
    idempotent_hint: bool
    open_world_hint: bool


BATCH_ANNOTATION_FIXTURES: list[BatchAnnotationFixture] = [
    BatchAnnotationFixture(
        test_id="mutating_batch_warns_destructive_open_world",
        tool_name="call_mutating_tools_batch",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
    ),
    BatchAnnotationFixture(
        test_id="destructive_batch_warns_destructive_open_world",
        tool_name="call_destructive_tools_batch",
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=True,
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

    @mcp.tool(
        title="Self Bounded Probe",
        annotations=ANNOTATIONS_RO,
        tags={TAG_READONLY, TAG_SELF_BOUNDED},
    )
    def self_bounded_probe(value: str) -> dict[str, str]:
        return {"value": value}

    return mcp


def _self_bounded_batch_call(wrapper: str, on_error: str = "stop") -> t.Any:
    """Call ``wrapper`` with a self-bounded op followed by a normal one."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                wrapper,
                {
                    "on_error": on_error,
                    "operations": [
                        {
                            "tool": "self_bounded_probe",
                            "arguments": {"value": "should-not-run"},
                        },
                        {
                            "tool": "readonly_probe",
                            "arguments": {"value": "kept-going"},
                        },
                    ],
                },
                raise_on_error=False,
            )

    return asyncio.run(_call())


@pytest.mark.parametrize(
    "wrapper",
    [
        "call_readonly_tools_batch",
        "call_mutating_tools_batch",
        "call_destructive_tools_batch",
    ],
)
def test_batch_rejects_self_bounded_tool_in_every_wrapper(wrapper: str) -> None:
    """A ``TAG_SELF_BOUNDED`` tool is rejected by ALL three batch wrappers.

    ``max_tier`` is a *ceiling* (``_TIER_LEVELS[tool_tier] <=
    _TIER_LEVELS[max_tier]``), so a readonly tool is reachable through
    the mutating and destructive wrappers too. The batch loop is serial
    with no aggregate deadline and ``MAX_BATCH_OPERATIONS`` is 1000, so
    a wait tool batched N times would cost N x its ceiling — the batch
    wrapper is a cap amplifier unless every wrapper rejects it.
    """
    result = _self_bounded_batch_call(wrapper)

    assert result.structured_content["failed"] == 1
    rows = result.structured_content["results"]
    assert rows[0]["success"] is False
    assert "cannot be batched" in rows[0]["error"]


def test_batch_self_bounded_rejection_preserves_continue_isolation() -> None:
    """The rejection is per-operation, so ``on_error='continue'`` still runs.

    Regression guard against implementing the exclusion as a pre-loop
    check: that would fail the whole batch and silently break
    ``on_error='continue'`` semantics for every unrelated operation in
    the request. The raise happens inside ``_call_one_tool``'s try
    block, so it becomes a ``success=False`` row instead.
    """
    result = _self_bounded_batch_call("call_readonly_tools_batch", on_error="continue")

    assert result.is_error is False
    assert result.structured_content["failed"] == 1
    assert result.structured_content["succeeded"] == 1
    rows = result.structured_content["results"]
    assert rows[0]["success"] is False
    assert rows[1]["success"] is True


def test_run_command_is_registered_self_bounded_and_unbatchable() -> None:
    """``run_command`` carries ``TAG_SELF_BOUNDED`` on the real server.

    ``run_command`` clamps its ``timeout`` to the same wait ceiling as
    the wait tools, so batching it amplifies that ceiling exactly the
    same way. Assert against the real registration rather than a probe,
    and drive ``_get_allowed_tool_tier`` at every wrapper's ``max_tier``
    because ``max_tier`` is a ceiling: a mutating tool is reachable
    through the mutating and destructive wrappers both.
    """
    from fastmcp import FastMCP

    from libtmux_mcp._utils import ExpectedToolError
    from libtmux_mcp.models import ToolCallOperation
    from libtmux_mcp.tools import register_tools
    from libtmux_mcp.tools.batch_tools import _get_allowed_tool_tier

    mcp = FastMCP(name="run-command-self-bounded-audit")
    register_tools(mcp)
    tool = asyncio.run(mcp.get_tool("run_command"))
    assert tool is not None
    assert TAG_MUTATING in tool.tags
    assert TAG_SELF_BOUNDED in tool.tags

    operation = ToolCallOperation(tool="run_command", arguments={})
    for max_tier in (TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE):
        with pytest.raises(ExpectedToolError, match="cannot be batched"):
            asyncio.run(
                _get_allowed_tool_tier(
                    fastmcp=mcp,
                    operation=operation,
                    max_tier=max_tier,
                )
            )


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
def test_call_readonly_tools_batch_rejects_oversized_operation_count(
    test_id: str,
    operation_count: int,
) -> None:
    """The batch wrapper rejects requests whose rows alone can exceed the cap."""
    from fastmcp import Client

    from libtmux_mcp.middleware import DEFAULT_RESPONSE_LIMIT_BYTES

    assert test_id

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_readonly_tools_batch",
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


@pytest.mark.parametrize(
    BatchAnnotationFixture._fields,
    BATCH_ANNOTATION_FIXTURES,
    ids=[fixture.test_id for fixture in BATCH_ANNOTATION_FIXTURES],
)
def test_batch_wrappers_advertise_worst_case_annotations(
    test_id: str,
    tool_name: str,
    read_only_hint: bool,
    destructive_hint: bool,
    idempotent_hint: bool,
    open_world_hint: bool,
) -> None:
    """Batch wrappers advertise the strongest hint from their allowed tools."""
    mcp = _batch_probe_server()

    tool = asyncio.run(mcp.get_tool(tool_name))
    assert tool is not None, f"{tool_name} should be registered"
    assert tool.annotations is not None, f"{tool_name} should carry annotations"
    assert tool.annotations.readOnlyHint is read_only_hint
    assert tool.annotations.destructiveHint is destructive_hint
    assert tool.annotations.idempotentHint is idempotent_hint
    assert tool.annotations.openWorldHint is open_world_hint
