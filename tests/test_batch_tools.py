"""Tests for generic MCP tool batching."""

from __future__ import annotations

import asyncio
import json
import typing as t

import pytest

from libtmux_mcp._utils import (
    ANNOTATIONS_CHANGE,
    ANNOTATIONS_DELETE,
    ANNOTATIONS_OBSERVE,
    TAG_SELF_BOUNDED,
    TOOLSET_EXECUTE,
    TOOLSET_INSPECT,
    TOOLSET_MANAGE,
    TOOLSET_TEARDOWN,
    VALID_TOOLSETS,
)
from tests.conftest import wire_annotations

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
        test_id="read_batch_carries_its_members_open_world",
        tool_name="call_read_tools_batch",
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
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

    from libtmux_mcp.middleware import ToolErrorResultMiddleware, ToolsetMiddleware
    from libtmux_mcp.tools.batch_tools import register as register_batch_tools

    mcp = FastMCP(
        name="batch-probe",
        middleware=[
            ToolErrorResultMiddleware(transform_errors=True),
            ToolsetMiddleware(set(VALID_TOOLSETS)),
        ],
    )
    register_batch_tools(mcp)

    @mcp.tool(
        title="Readonly Probe", annotations=ANNOTATIONS_OBSERVE, tags={TOOLSET_INSPECT}
    )
    def readonly_probe(value: str) -> dict[str, str]:
        return {"value": value}

    @mcp.tool(
        title="Mutating Probe",
        annotations=ANNOTATIONS_CHANGE,
        tags={TOOLSET_MANAGE},
    )
    def mutating_probe(value: str) -> dict[str, str]:
        return {"value": value}

    @mcp.tool(
        title="Destructive Probe",
        annotations=ANNOTATIONS_DELETE,
        tags={TOOLSET_TEARDOWN},
    )
    def destructive_probe(value: str) -> dict[str, str]:
        return {"value": value}

    @mcp.tool(
        title="Self Bounded Probe",
        annotations=ANNOTATIONS_OBSERVE,
        tags={TOOLSET_INSPECT, TAG_SELF_BOUNDED},
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
                "call_read_tools_batch",
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


def test_batch_rejects_a_self_bounded_tool() -> None:
    """A ``TAG_SELF_BOUNDED`` tool is rejected by the batch wrapper.

    ``max_tier`` is a *ceiling* (``_TIER_LEVELS[tool_tier] <=
    _TIER_LEVELS[max_tier]``), so a readonly tool is reachable through
    the mutating and destructive wrappers too. The batch loop is serial
    with no aggregate deadline and ``MAX_BATCH_OPERATIONS`` is 1000, so
    a wait tool batched N times would cost N x its ceiling — the batch
    wrapper is a cap amplifier unless every wrapper rejects it.
    """
    result = _self_bounded_batch_call("call_read_tools_batch")

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
    result = _self_bounded_batch_call("call_read_tools_batch", on_error="continue")

    assert result.is_error is False
    assert result.structured_content["failed"] == 1
    assert result.structured_content["succeeded"] == 1
    rows = result.structured_content["results"]
    assert rows[0]["success"] is False
    assert rows[1]["success"] is True


def test_run_command_is_registered_self_bounded_and_unbatchable() -> None:
    """``run_command`` enforces its own ceiling, so a batch cannot multiply it.

    Assert against the real registration rather than a probe: the tag is
    what keeps the batch loop, which has no aggregate deadline, from
    running it a thousand times.
    """
    from fastmcp import FastMCP

    from libtmux_mcp._utils import ExpectedToolError
    from libtmux_mcp.models import ToolCallOperation
    from libtmux_mcp.tools import register_tools
    from libtmux_mcp.tools.batch_tools import _check_operation_allowed

    mcp = FastMCP(name="run-command-self-bounded-audit")
    register_tools(mcp)
    tool = asyncio.run(mcp.get_tool("run_command"))
    assert tool is not None
    assert TOOLSET_EXECUTE in tool.tags
    assert TAG_SELF_BOUNDED in tool.tags

    operation = ToolCallOperation(tool="run_command", arguments={})
    with pytest.raises(ExpectedToolError, match="cannot be batched"):
        asyncio.run(_check_operation_allowed(fastmcp=mcp, operation=operation))


def test_call_readonly_tools_batch_preserves_structured_results() -> None:
    """The readonly batch wrapper returns per-tool structured content."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_read_tools_batch",
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
                "call_read_tools_batch",
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
                "call_read_tools_batch",
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


def test_the_read_batch_rejects_a_tool_outside_inspect() -> None:
    """A batch that could carry a write would launder it past client policy.

    The wrapper aggregates authority under its own name, so a rule keyed
    on a nested tool's name never fires. Keeping the batch to `inspect`
    is what stops that mattering.
    """
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_read_tools_batch",
                {"operations": [{"tool": "mutating_probe", "arguments": {}}]},
                raise_on_error=False,
            )

    payload = asyncio.run(_call()).structured_content

    assert payload["succeeded"] == 0
    assert "not an 'inspect' tool" in payload["results"][0]["error"]


def test_call_tools_batch_rejects_self_invocation() -> None:
    """Batch wrappers cannot recursively call batch wrappers."""
    from fastmcp import Client

    async def _call() -> t.Any:
        async with Client(_batch_probe_server()) as client:
            return await client.call_tool(
                "call_read_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "call_read_tools_batch",
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
    assert wire_annotations(tool).get("readOnlyHint") is read_only_hint
    assert wire_annotations(tool).get("destructiveHint") is destructive_hint
    assert wire_annotations(tool).get("idempotentHint") is idempotent_hint
    assert wire_annotations(tool).get("openWorldHint") is open_world_hint
