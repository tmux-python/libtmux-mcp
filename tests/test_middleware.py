"""Tests for libtmux MCP safety + audit middleware."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import typing as t

import pydantic
import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from libtmux import exc as libtmux_exc
from mcp.types import CallToolRequestParams

from libtmux_mcp._utils import TAG_DESTRUCTIVE, TAG_MUTATING, TAG_READONLY
from libtmux_mcp.middleware import (
    AuditMiddleware,
    ReadonlyRetryMiddleware,
    SafetyMiddleware,
    _redact_digest,
    _summarize_args,
)


class SafetyAllowedFixture(t.NamedTuple):
    """Test fixture for SafetyMiddleware._is_allowed."""

    test_id: str
    max_tier: str
    tool_tags: set[str]
    expected_allowed: bool


SAFETY_ALLOWED_FIXTURES: list[SafetyAllowedFixture] = [
    # readonly tier: only readonly tools allowed
    SafetyAllowedFixture(
        test_id="readonly_allows_readonly",
        max_tier=TAG_READONLY,
        tool_tags={TAG_READONLY},
        expected_allowed=True,
    ),
    SafetyAllowedFixture(
        test_id="readonly_blocks_mutating",
        max_tier=TAG_READONLY,
        tool_tags={TAG_MUTATING},
        expected_allowed=False,
    ),
    SafetyAllowedFixture(
        test_id="readonly_blocks_destructive",
        max_tier=TAG_READONLY,
        tool_tags={TAG_DESTRUCTIVE},
        expected_allowed=False,
    ),
    # mutating tier: readonly + mutating allowed
    SafetyAllowedFixture(
        test_id="mutating_allows_readonly",
        max_tier=TAG_MUTATING,
        tool_tags={TAG_READONLY},
        expected_allowed=True,
    ),
    SafetyAllowedFixture(
        test_id="mutating_allows_mutating",
        max_tier=TAG_MUTATING,
        tool_tags={TAG_MUTATING},
        expected_allowed=True,
    ),
    SafetyAllowedFixture(
        test_id="mutating_blocks_destructive",
        max_tier=TAG_MUTATING,
        tool_tags={TAG_DESTRUCTIVE},
        expected_allowed=False,
    ),
    # destructive tier: all allowed
    SafetyAllowedFixture(
        test_id="destructive_allows_readonly",
        max_tier=TAG_DESTRUCTIVE,
        tool_tags={TAG_READONLY},
        expected_allowed=True,
    ),
    SafetyAllowedFixture(
        test_id="destructive_allows_mutating",
        max_tier=TAG_DESTRUCTIVE,
        tool_tags={TAG_MUTATING},
        expected_allowed=True,
    ),
    SafetyAllowedFixture(
        test_id="destructive_allows_destructive",
        max_tier=TAG_DESTRUCTIVE,
        tool_tags={TAG_DESTRUCTIVE},
        expected_allowed=True,
    ),
    # untagged tools are denied (fail-closed)
    SafetyAllowedFixture(
        test_id="untagged_denied_at_readonly",
        max_tier=TAG_READONLY,
        tool_tags=set(),
        expected_allowed=False,
    ),
]


@pytest.mark.parametrize(
    SafetyAllowedFixture._fields,
    SAFETY_ALLOWED_FIXTURES,
    ids=[f.test_id for f in SAFETY_ALLOWED_FIXTURES],
)
def test_safety_middleware_is_allowed(
    test_id: str,
    max_tier: str,
    tool_tags: set[str],
    expected_allowed: bool,
) -> None:
    """SafetyMiddleware._is_allowed gates tools by tier."""
    mw = SafetyMiddleware(max_tier=max_tier)
    assert mw._is_allowed(tool_tags) is expected_allowed


def test_safety_middleware_default_tier() -> None:
    """SafetyMiddleware defaults to mutating tier."""
    mw = SafetyMiddleware()
    assert mw._is_allowed({TAG_READONLY}) is True
    assert mw._is_allowed({TAG_MUTATING}) is True
    assert mw._is_allowed({TAG_DESTRUCTIVE}) is False


def test_safety_middleware_invalid_tier_falls_back() -> None:
    """SafetyMiddleware falls back to readonly for unknown tiers."""
    mw = SafetyMiddleware(max_tier="nonexistent")
    assert mw._is_allowed({TAG_READONLY}) is True
    assert mw._is_allowed({TAG_MUTATING}) is False
    assert mw._is_allowed({TAG_DESTRUCTIVE}) is False


# ---------------------------------------------------------------------------
# AuditMiddleware
# ---------------------------------------------------------------------------


def test_redact_digest_shape() -> None:
    """_redact_digest reports length and a 12-char sha256 prefix."""
    payload = "rm -rf /"
    digest = _redact_digest(payload)
    assert digest["len"] == len(payload)
    assert digest["sha256_prefix"] == hashlib.sha256(payload.encode()).hexdigest()[:12]


def test_summarize_args_redacts_sensitive_keys() -> None:
    """Sensitive arg names get replaced by a digest dict, not raw string."""
    args: dict[str, t.Any] = {
        "keys": "rm -rf /",
        "text": "hello world",
        "command": "psql -U user -W secret123 mydb",
        "value": "supersecret",
        "content": "buffer payload",
        "shell": "psql -U user -W secret123 mydb",
        "pane_id": "%1",
        "bracket": True,
    }
    summary = _summarize_args(args)
    for sensitive in ("keys", "text", "command", "value", "content", "shell"):
        assert isinstance(summary[sensitive], dict)
        assert "len" in summary[sensitive]
        assert "sha256_prefix" in summary[sensitive]
        raw_value = args[sensitive]
        assert isinstance(raw_value, str)
        assert raw_value not in str(summary[sensitive])
    # Non-sensitive args pass through unchanged.
    assert summary["pane_id"] == "%1"
    assert summary["bracket"] is True


class CommandRedactionFixture(t.NamedTuple):
    """Test fixture for _summarize_args redaction of run_command's command."""

    test_id: str
    command: str


class MalformedOperationAuditFixture(t.NamedTuple):
    """Test fixture for malformed send_keys_batch audit entries."""

    test_id: str
    operation: object
    forbidden_text: str
    expected_type: str


class MalformedOperationsPayloadAuditFixture(t.NamedTuple):
    """Test fixture for malformed send_keys_batch audit payloads."""

    test_id: str
    args: dict[str, object]
    forbidden_text: str
    expected_shape: t.Literal["redacted_container", "redacted_unknown_field"]


class BatchSchemaValidationRedactionFixture(t.NamedTuple):
    """Test fixture for schema-validation redaction of batch payloads."""

    test_id: str
    arguments: dict[str, t.Any]
    secret: str
    expected_fragments: tuple[str, ...]


COMMAND_REDACTION_FIXTURES: list[CommandRedactionFixture] = [
    CommandRedactionFixture(
        test_id="credential_bearing",
        command="psql -U admin -W supersecret mydb",
    ),
    CommandRedactionFixture(
        test_id="plain",
        command="ls -la /tmp",
    ),
]


MALFORMED_OPERATION_AUDIT_FIXTURES: list[MalformedOperationAuditFixture] = [
    MalformedOperationAuditFixture(
        test_id="string_operation",
        operation="keys=printf SECRET_COMMAND",
        forbidden_text="SECRET_COMMAND",
        expected_type="str",
    ),
    MalformedOperationAuditFixture(
        test_id="list_operation",
        operation=["keys=printf SECRET_COMMAND"],
        forbidden_text="SECRET_COMMAND",
        expected_type="list",
    ),
]


MALFORMED_OPERATIONS_PAYLOAD_AUDIT_FIXTURES: list[
    MalformedOperationsPayloadAuditFixture
] = [
    MalformedOperationsPayloadAuditFixture(
        test_id="operations_object",
        args={"operations": {"keys": "printf SECRET_OBJECT", "pane_id": "%1"}},
        forbidden_text="SECRET_OBJECT",
        expected_shape="redacted_container",
    ),
    MalformedOperationsPayloadAuditFixture(
        test_id="unknown_operation_field",
        args={"operations": [{"key": "printf SECRET_KEY", "pane_id": "%1"}]},
        forbidden_text="SECRET_KEY",
        expected_shape="redacted_unknown_field",
    ),
]


BATCH_SCHEMA_VALIDATION_REDACTION_FIXTURES: list[
    BatchSchemaValidationRedactionFixture
] = [
    BatchSchemaValidationRedactionFixture(
        test_id="unknown_operation_field",
        arguments={
            "operations": [{"key": "printf SECRET_SCHEMA_KEY", "pane_id": "%1"}]
        },
        secret="SECRET_SCHEMA_KEY",
        expected_fragments=("operations.0.key", "extra_forbidden"),
    ),
    BatchSchemaValidationRedactionFixture(
        test_id="operations_object",
        arguments={
            "operations": {"keys": "printf SECRET_SCHEMA_OBJECT", "pane_id": "%1"}
        },
        secret="SECRET_SCHEMA_OBJECT",
        expected_fragments=("operations", "list_type"),
    ),
]


@pytest.mark.parametrize(
    CommandRedactionFixture._fields,
    COMMAND_REDACTION_FIXTURES,
    ids=[f.test_id for f in COMMAND_REDACTION_FIXTURES],
)
def test_summarize_args_redacts_command(test_id: str, command: str) -> None:
    """run_command's command payload is digested, not logged in cleartext."""
    summary = _summarize_args({"command": command})
    assert isinstance(summary["command"], dict)
    assert "len" in summary["command"]
    assert "sha256_prefix" in summary["command"]
    assert command not in str(summary["command"])


def test_summarize_args_redacts_sensitive_dict_values() -> None:
    """Dict-shaped sensitive args keep keys but digest values per-entry.

    ``environment`` on ``respawn_pane`` is a ``dict[str, str]``. The
    values typically carry secrets (DB passwords, API keys), but the
    keys (``DATABASE_URL``, ``AWS_SECRET_KEY``) are operator-useful for
    debugging which env var was set. The redaction policy preserves
    keys and digests values.
    """
    args: dict[str, t.Any] = {
        "environment": {
            "DATABASE_URL": "postgres://user:hunter2@db/app",
            "AWS_SECRET_KEY": "AKIAIOSFODNN7EXAMPLE",
        },
        "pane_id": "%1",
    }
    summary = _summarize_args(args)
    assert isinstance(summary["environment"], dict)
    assert set(summary["environment"].keys()) == {"DATABASE_URL", "AWS_SECRET_KEY"}
    for key in ("DATABASE_URL", "AWS_SECRET_KEY"):
        digest = summary["environment"][key]
        assert isinstance(digest, dict)
        assert "len" in digest
        assert "sha256_prefix" in digest
    # No value bytes leak into the rendered summary.
    rendered = str(summary)
    assert "hunter2" not in rendered
    assert "AKIAIOSFODNN7EXAMPLE" not in rendered
    # Non-sensitive args still pass through.
    assert summary["pane_id"] == "%1"


def test_summarize_args_redacts_send_keys_batch_operations() -> None:
    """send_keys_batch operation payloads are digested inside the list."""
    args: dict[str, t.Any] = {
        "operations": [
            {"keys": "psql -U admin -W supersecret mydb", "pane_id": "%1"},
            {"keys": "printf public", "pane_id": "%2", "enter": False},
        ],
        "on_error": "continue",
    }

    summary = _summarize_args(args)
    rendered = str(summary)

    assert "supersecret" not in rendered
    assert "printf public" not in rendered
    first = summary["operations"][0]
    second = summary["operations"][1]
    assert first["pane_id"] == "%1"
    assert second["pane_id"] == "%2"
    assert second["enter"] is False
    for operation in (first, second):
        assert isinstance(operation["keys"], dict)
        assert "len" in operation["keys"]
        assert "sha256_prefix" in operation["keys"]


def test_summarize_args_redacts_nested_tool_batch_arguments() -> None:
    """Generic batch operations preserve tool names while digesting payloads."""
    args: dict[str, t.Any] = {
        "operations": [
            {
                "tool": "send_keys",
                "arguments": {
                    "keys": "psql -U admin -W supersecret mydb",
                    "pane_id": "%1",
                },
            },
            {
                "tool": "set_environment",
                "arguments": {
                    "name": "DATABASE_URL",
                    "value": "postgres://admin:topsecret@db/prod",
                },
            },
        ],
    }

    summary = _summarize_args(args)
    rendered = str(summary)

    assert "supersecret" not in rendered
    assert "topsecret" not in rendered
    first, second = summary["operations"]
    assert first["tool"] == "send_keys"
    assert first["arguments"]["pane_id"] == "%1"
    assert isinstance(first["arguments"]["keys"], dict)
    assert second["tool"] == "set_environment"
    assert second["arguments"]["name"] == "DATABASE_URL"
    assert isinstance(second["arguments"]["value"], dict)


@pytest.mark.parametrize(
    MalformedOperationAuditFixture._fields,
    MALFORMED_OPERATION_AUDIT_FIXTURES,
    ids=[fixture.test_id for fixture in MALFORMED_OPERATION_AUDIT_FIXTURES],
)
def test_summarize_args_redacts_malformed_send_keys_batch_operation_entries(
    test_id: str,
    operation: object,
    forbidden_text: str,
    expected_type: str,
) -> None:
    """Malformed send_keys_batch operation entries do not leak raw payloads."""
    assert test_id
    summary = _summarize_args({"operations": [operation]})
    rendered = str(summary)

    assert forbidden_text not in rendered
    item = summary["operations"][0]
    assert isinstance(item, dict)
    assert item["type"] == expected_type
    assert item["redacted"] is True


@pytest.mark.parametrize(
    MalformedOperationsPayloadAuditFixture._fields,
    MALFORMED_OPERATIONS_PAYLOAD_AUDIT_FIXTURES,
    ids=[fixture.test_id for fixture in MALFORMED_OPERATIONS_PAYLOAD_AUDIT_FIXTURES],
)
def test_summarize_args_redacts_malformed_send_keys_batch_operation_payloads(
    test_id: str,
    args: dict[str, object],
    forbidden_text: str,
    expected_shape: t.Literal["redacted_container", "redacted_unknown_field"],
) -> None:
    """Malformed send_keys_batch operation payloads do not leak raw values."""
    assert test_id
    summary = _summarize_args(args)
    rendered = str(summary)

    assert forbidden_text not in rendered
    operations = summary["operations"]
    if expected_shape == "redacted_container":
        assert operations == {"type": "dict", "redacted": True}
    else:
        assert isinstance(operations, list)
        item = operations[0]
        assert isinstance(item, dict)
        assert item["pane_id"] == "%1"
        assert item["key"] == {"type": "str", "redacted": True}


def test_summarize_args_truncates_long_non_sensitive_strings() -> None:
    """Non-sensitive strings over the cap get truncated with a marker."""
    args = {"output_path": "x" * 500}
    summary = _summarize_args(args)
    assert summary["output_path"].endswith("...<truncated>")
    assert len(summary["output_path"]) < 500


@pytest.mark.parametrize(
    BatchSchemaValidationRedactionFixture._fields,
    BATCH_SCHEMA_VALIDATION_REDACTION_FIXTURES,
    ids=[fixture.test_id for fixture in BATCH_SCHEMA_VALIDATION_REDACTION_FIXTURES],
)
def test_send_keys_batch_schema_validation_redacts_inputs(
    test_id: str,
    arguments: dict[str, t.Any],
    secret: str,
    expected_fragments: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed batch schema errors do not echo raw key payloads."""
    from fastmcp import Client

    from libtmux_mcp.server import build_mcp_server

    assert test_id

    async def _call() -> t.Any:
        async with Client(build_mcp_server()) as client:
            return await client.call_tool(
                "send_keys_batch",
                arguments,
                raise_on_error=False,
            )

    with (
        caplog.at_level(logging.WARNING, logger="fastmcp.server.server"),
        caplog.at_level(logging.WARNING, logger="fastmcp.errors"),
    ):
        result = asyncio.run(_call())

    assert result.is_error is True
    result_text = result.content[0].text
    logs_text = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name in {"fastmcp.server.server", "fastmcp.errors"}
    )

    assert secret not in result_text
    assert secret not in logs_text
    assert "input_value" not in result_text
    assert "'input':" not in logs_text
    for fragment in expected_fragments:
        assert fragment in result_text or fragment in logs_text


class _RecordingCallNext:
    """Minimal async callable that records invocation and returns a value."""

    def __init__(self, result: t.Any = "ok", raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.calls = 0

    async def __call__(self, _context: t.Any) -> t.Any:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result


def _fake_context(
    name: str = "some_tool",
    arguments: dict[str, t.Any] | None = None,
) -> MiddlewareContext[CallToolRequestParams]:
    """Build a real MiddlewareContext for audit tests.

    ``fastmcp_context`` is left as ``None`` — the real
    :class:`fastmcp.Context` requires a live FastMCP server and MCP
    request context to construct, which is out of scope for a unit
    test. The audit middleware handles ``fastmcp_context=None``
    cleanly; ``client_id`` and ``request_id`` just stay ``None`` in the
    log record, which is exactly the production code path when no
    upstream MCP context is attached.
    """
    return MiddlewareContext(
        message=CallToolRequestParams(name=name, arguments=arguments or {}),
    )


def test_audit_middleware_logs_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """on_call_tool emits an 'outcome=ok' record with duration_ms."""
    mw = AuditMiddleware()
    ctx = _fake_context(name="list_sessions", arguments={"socket_name": "test"})
    call_next = _RecordingCallNext(result="ok")

    with caplog.at_level(logging.INFO, logger="libtmux_mcp.audit"):
        result = asyncio.run(mw.on_call_tool(ctx, call_next))

    assert result == "ok"
    assert call_next.calls == 1
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("tool=list_sessions" in m and "outcome=ok" in m for m in messages)
    assert any("duration_ms=" in m for m in messages)
    assert any("client_id=None" in m for m in messages)


def test_audit_middleware_logs_error_and_reraises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Errors are logged with error_type and then re-raised."""
    mw = AuditMiddleware()
    ctx = _fake_context(name="kill_server", arguments={})
    call_next = _RecordingCallNext(raises=RuntimeError("boom"))

    with (
        caplog.at_level(logging.INFO, logger="libtmux_mcp.audit"),
        pytest.raises(RuntimeError, match="boom"),
    ):
        asyncio.run(mw.on_call_tool(ctx, call_next))

    messages = [rec.getMessage() for rec in caplog.records]
    assert any(
        "tool=kill_server" in m
        and "outcome=error" in m
        and "error_type=RuntimeError" in m
        for m in messages
    )


def test_audit_middleware_redacts_sensitive_args(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Raw sensitive payloads must never appear in the log record."""
    mw = AuditMiddleware()
    payload = "SECRET_KEYS_PAYLOAD_12345"
    ctx = _fake_context(
        name="send_keys",
        arguments={"pane_id": "%1", "keys": payload},
    )

    with caplog.at_level(logging.INFO, logger="libtmux_mcp.audit"):
        asyncio.run(mw.on_call_tool(ctx, _RecordingCallNext(result="ok")))

    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    assert payload not in rendered
    assert "sha256_prefix" in rendered
    assert "tool=send_keys" in rendered


# ---------------------------------------------------------------------------
# TailPreservingResponseLimitingMiddleware tests
# ---------------------------------------------------------------------------


def test_tail_preserving_truncation_keeps_tail() -> None:
    """Over-sized text is truncated from the head, tail is preserved."""
    from mcp.types import TextContent

    from libtmux_mcp.middleware import TailPreservingResponseLimitingMiddleware

    mw = TailPreservingResponseLimitingMiddleware(max_size=200)
    # Build a string much larger than the cap; mark the tail so we can
    # assert the last chunk survived. ``HEAD_`` lines are old; ``TAIL_``
    # line is the active prompt.
    payload = ("HEAD_OLDER\n" * 200) + "TAIL_PROMPT $"
    result = mw._truncate_to_result(payload)
    assert isinstance(result.content[0], TextContent)
    text = result.content[0].text
    assert text.startswith("[... truncated ")
    first_line, _, _ = text.partition("\n")
    assert first_line.endswith(" bytes ...]")
    # The most recent content must survive.
    assert "TAIL_PROMPT $" in text
    # The result is bounded.
    assert len(text.encode("utf-8")) <= 200


def test_tail_preserving_passthrough_when_under_cap() -> None:
    """Text within the cap passes through unchanged."""
    from mcp.types import TextContent

    from libtmux_mcp.middleware import TailPreservingResponseLimitingMiddleware

    mw = TailPreservingResponseLimitingMiddleware(max_size=10_000)
    payload = "short output\n$ "
    result = mw._truncate_to_result(payload)
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == payload


# ---------------------------------------------------------------------------
# Middleware stack composition tests
# ---------------------------------------------------------------------------


def test_server_middleware_stack_order() -> None:
    """The production middleware stack is wired in the intended order.

    The ordering is load-bearing (see server.py comment):
    TimingMiddleware must be outermost so it observes total wall
    time; AuditMiddleware must sit *outside* SafetyMiddleware so
    tier-denial events (which raise ``ExpectedToolError`` before
    ``call_next``) are still recorded — without this ordering,
    forbidden-access attempts silently bypass the audit log. A
    refactor that swaps Audit and Safety would degrade
    security observability without an obvious test failure, so pin
    the sequence explicitly.

    ReadonlyRetryMiddleware sits between Audit and Safety so retried
    calls are audited once each (Audit wraps the retry loop) and
    tier-denied tools never reach retry (Safety stops them first).
    """
    from fastmcp.server.middleware.timing import TimingMiddleware

    from libtmux_mcp.middleware import (
        AuditMiddleware,
        ReadonlyRetryMiddleware,
        SafetyMiddleware,
        TailPreservingResponseLimitingMiddleware,
        ToolErrorResultMiddleware,
    )
    from libtmux_mcp.server import mcp

    types = [type(mw) for mw in mcp.middleware]
    # FastMCP auto-appends an internal DereferenceRefsMiddleware at the
    # end of the stack; we care about the ordering of the middleware
    # *we* configured. Slice off the suffix before comparing.
    assert types[:6] == [
        TimingMiddleware,
        TailPreservingResponseLimitingMiddleware,
        ToolErrorResultMiddleware,
        AuditMiddleware,
        ReadonlyRetryMiddleware,
        SafetyMiddleware,
    ]


def test_error_handling_middleware_transforms_errors() -> None:
    """ToolErrorResultMiddleware is configured with transform_errors=True.

    Regression guard: without ``transform_errors=True`` the inherited
    ``on_message`` would still log but not map resource errors to MCP
    error code ``-32002``, which is the protocol-correctness point of
    keeping the ErrorHandlingMiddleware base for non-tool messages
    (tool-call errors are converted to ``is_error`` results before
    they reach ``on_message``).
    """
    from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware

    from libtmux_mcp.server import mcp

    err_mw = next(
        mw for mw in mcp.middleware if isinstance(mw, ErrorHandlingMiddleware)
    )
    assert err_mw.transform_errors is True


def test_audit_records_safety_denial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tool denied by SafetyMiddleware still appears in the audit log.

    Composes Audit and Safety in the production order (Audit outside
    Safety) by manually nesting their ``on_call_tool`` handlers: the
    inner ``call_next`` from Audit dispatches to Safety, which raises
    ``ExpectedToolError`` for an over-tier tool. Audit should record
    that as ``outcome=error error_type=ExpectedToolError`` rather
    than skipping the record. Without this ordering, denied access
    attempts would silently bypass forensic logging.
    """
    from libtmux_mcp._utils import ExpectedToolError

    audit = AuditMiddleware()
    ctx = _fake_context(name="kill_server", arguments={})

    # SafetyMiddleware.on_call_tool consults
    # context.fastmcp_context.fastmcp.get_tool(...). With
    # fastmcp_context=None the safety check short-circuits, so we
    # simulate the denial more directly: ``call_next`` is a coroutine
    # that raises the same ``ExpectedToolError`` SafetyMiddleware
    # would when blocking an over-tier call. The test's invariant is
    # that the AuditMiddleware sitting *outside* Safety still records
    # the attempt with outcome=error.
    msg = "Tool 'kill_server' is not available at the current safety level."

    async def _safety_denial(_ctx: t.Any) -> None:
        raise ExpectedToolError(msg)

    with (
        caplog.at_level(logging.INFO, logger="libtmux_mcp.audit"),
        pytest.raises(ExpectedToolError, match="not available"),
    ):
        asyncio.run(audit.on_call_tool(ctx, _safety_denial))

    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "tool=kill_server" in rendered
    assert "outcome=error" in rendered
    assert "error_type=ExpectedToolError" in rendered


# ---------------------------------------------------------------------------
# ReadonlyRetryMiddleware tests
# ---------------------------------------------------------------------------


class _StubTool:
    """Minimal tool stand-in for ``get_tool`` lookups."""

    def __init__(self, tags: set[str]) -> None:
        self.tags = tags


class _StubFastMCP:
    """Awaitable ``get_tool`` returning a single stub tool."""

    def __init__(self, tool: _StubTool) -> None:
        self._tool = tool

    async def get_tool(self, _name: str) -> _StubTool:
        return self._tool


class _StubFastMCPContext:
    """Just enough to satisfy ``context.fastmcp_context.fastmcp.get_tool``."""

    def __init__(self, fastmcp: _StubFastMCP) -> None:
        self.fastmcp = fastmcp


def _retry_context(tags: set[str]) -> MiddlewareContext[CallToolRequestParams]:
    """Build a MiddlewareContext that returns a tool with the given tags."""
    fastmcp_context = _StubFastMCPContext(_StubFastMCP(_StubTool(tags)))
    return MiddlewareContext(
        message=CallToolRequestParams(name="x", arguments={}),
        fastmcp_context=t.cast("t.Any", fastmcp_context),
    )


class _FlakyCallNext:
    """Async callable that raises N times before succeeding."""

    def __init__(self, raises_n_times: int, exception: Exception) -> None:
        self.exception = exception
        self.remaining = raises_n_times
        self.calls = 0

    async def __call__(self, _context: t.Any) -> str:
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exception
        return "ok"


def test_readonly_retry_recovers_from_libtmux_exception() -> None:
    """Readonly tool is retried once on ``LibTmuxException`` and succeeds.

    Models the production scenario the middleware exists to fix: a
    transient socket error from libtmux on the first call, then a
    successful call after the cache evicts the dead Server. Without
    the retry the agent would see an expected tool error on the first
    ``list_sessions``-style call.
    """
    from libtmux import exc as libtmux_exc

    middleware = ReadonlyRetryMiddleware(max_retries=1, base_delay=0.0)
    ctx = _retry_context(tags={TAG_READONLY})
    call_next = _FlakyCallNext(
        raises_n_times=1,
        exception=libtmux_exc.LibTmuxException("transient socket error"),
    )

    result = asyncio.run(middleware.on_call_tool(ctx, call_next))

    assert result == "ok"
    assert call_next.calls == 2  # initial failure + one retry


def test_readonly_retry_skips_mutating_tool() -> None:
    """Mutating tool is NOT retried on ``LibTmuxException``.

    Critical safety property: re-running ``send_keys``,
    ``create_session``, or any other mutating call on a transient
    error would silently double the side effect. This test pins the
    "no retry for non-readonly" gate.
    """
    from libtmux import exc as libtmux_exc

    middleware = ReadonlyRetryMiddleware(max_retries=1, base_delay=0.0)
    ctx = _retry_context(tags={TAG_MUTATING})
    call_next = _FlakyCallNext(
        raises_n_times=1,
        exception=libtmux_exc.LibTmuxException("transient socket error"),
    )

    with pytest.raises(libtmux_exc.LibTmuxException, match="transient"):
        asyncio.run(middleware.on_call_tool(ctx, call_next))

    assert call_next.calls == 1  # no retry — fail on first call


def test_readonly_retry_skips_non_libtmux_exception() -> None:
    """Even readonly tools don't retry on exceptions outside the trigger set.

    Default ``retry_exceptions=(LibTmuxException,)`` is narrow on
    purpose — a ``ValueError`` from caller-side input is a
    programming error, not a transient socket hiccup, and retrying
    it would just delay the real failure.
    """
    middleware = ReadonlyRetryMiddleware(max_retries=1, base_delay=0.0)
    ctx = _retry_context(tags={TAG_READONLY})
    call_next = _FlakyCallNext(
        raises_n_times=1,
        exception=ValueError("bad caller input"),
    )

    with pytest.raises(ValueError, match="bad caller input"):
        asyncio.run(middleware.on_call_tool(ctx, call_next))

    assert call_next.calls == 1  # no retry — wrong exception type


def test_readonly_retry_recovers_on_decorated_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: retry fires through the production decorator wrap path.

    Regression guard for the fastmcp <3.2.4 production no-op where
    ``RetryMiddleware._should_retry`` did not walk ``__cause__``.
    Every libtmux-mcp tool is wrapped by ``handle_tool_errors`` /
    ``handle_tool_errors_async``, which converts ``LibTmuxException``
    to ``ExpectedToolError(...) from LibTmuxException``. At the
    middleware layer the exception type is ``ExpectedToolError``, not
    ``LibTmuxException`` — so the retry decision must walk
    ``__cause__`` to see the real failure type.

    The unit tests above use ``_FlakyCallNext`` which raises
    ``LibTmuxException`` directly, bypassing the decorator. They
    pass on every fastmcp version. This test invokes the real
    ``list_sessions`` tool through the middleware, exercising the
    decorator wrap path that broke in production:

    * On fastmcp 3.2.3: would fail with ``calls == 1`` (no retry).
    * On fastmcp >= 3.2.4: passes with ``calls == 2``.

    The ``pyproject.toml`` floor (``fastmcp>=3.2.4``) keeps this
    test green; an accidental downgrade would re-introduce the bug
    and fail this test loudly.
    """
    from libtmux import Server, exc as libtmux_exc

    from libtmux_mcp.tools.server_tools import list_sessions

    calls = {"count": 0}

    def _flaky_sessions(_self: Server) -> list[t.Any]:
        calls["count"] += 1
        if calls["count"] == 1:
            msg = "transient socket error"
            raise libtmux_exc.LibTmuxException(msg)
        return []  # second call succeeds with no tmux required

    monkeypatch.setattr(Server, "sessions", property(_flaky_sessions))

    middleware = ReadonlyRetryMiddleware(max_retries=1, base_delay=0.0)
    ctx = _retry_context(tags={TAG_READONLY})

    async def real_call_next(_context: t.Any) -> t.Any:
        # ``list_sessions`` is sync + ``@handle_tool_errors`` decorated.
        # Wrapping the sync call in an async function is enough — the
        # exception path is what we care about.
        return list_sessions(socket_name="retry-integration-smoke")

    result = asyncio.run(middleware.on_call_tool(ctx, real_call_next))

    assert result == []
    assert calls["count"] == 2, (
        f"retry did not fire (calls={calls['count']}). Likely cause: "
        f"fastmcp<3.2.4 RetryMiddleware._should_retry not walking "
        f"__cause__. Bump pyproject.toml fastmcp pin to >=3.2.4."
    )


@pytest.mark.parametrize(
    "raised",
    [
        libtmux_exc.TmuxObjectDoesNotExist("@99"),
        libtmux_exc.MultipleObjectsReturned(count=2, query={"pane_id": "%0"}),
        libtmux_exc.PaneNotFound("%99"),
        libtmux_exc.NoWindowsExist,
        libtmux_exc.BadSessionName(reason="contains periods", session_name="a.b"),
        libtmux_exc.TmuxSessionExists("session exists"),
    ],
    ids=lambda e: type(e).__name__ if isinstance(e, Exception) else e.__name__,
)
def test_readonly_retry_skips_deterministic_failures(raised: Exception) -> None:
    """A failure a second attempt cannot change is not retried.

    Every one of these descends from ``LibTmuxException``, which is the retry
    trigger — so without :data:`NON_RETRYABLE_EXCEPTIONS` they would all be
    retried. None of them can succeed on the second look: a pane that is not
    there will not appear during a backoff window, and an ambiguous match does
    not become unambiguous. Retrying buys a second tmux round-trip and 100 ms
    of latency in order to fail identically.
    """
    middleware = ReadonlyRetryMiddleware(max_retries=1, base_delay=0.0)
    ctx = _retry_context(tags={TAG_READONLY})
    call_next = _FlakyCallNext(raises_n_times=1, exception=raised)

    with pytest.raises(libtmux_exc.LibTmuxException):
        asyncio.run(middleware.on_call_tool(ctx, call_next))

    assert call_next.calls == 1, (
        f"{type(raised).__name__} was retried. It descends from LibTmuxException, "
        f"so it must be listed in NON_RETRYABLE_EXCEPTIONS."
    )


def test_readonly_retry_skips_not_found_on_decorated_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a stale id is not retried through the production wrap path.

    The unit test above raises straight into the middleware. This one goes
    through ``handle_tool_errors``, which re-raises every libtmux failure as an
    ``ExpectedToolError`` chained off the original — so at the middleware layer
    the exception is an ``ExpectedToolError`` and the real failure is only
    visible one hop down, on ``__cause__``. The retry decision walks that hop
    (which is what makes retries work at all), so the *skip* decision has to
    walk it too, or it never fires in production.

    Guards the shape libtmux tmux-python/libtmux#718 introduced:
    ``TmuxObjectDoesNotExist`` became a ``LibTmuxException``, which silently
    made every stale session or window id retryable.
    """
    from libtmux import Server

    from libtmux_mcp.tools.server_tools import list_sessions

    calls = {"count": 0}

    def _missing_sessions(_self: Server) -> list[t.Any]:
        calls["count"] += 1
        raise libtmux_exc.TmuxObjectDoesNotExist(
            obj_key="session_id",
            obj_id="$99",
            list_cmd="list-sessions",
            list_extra_args=None,
        )

    monkeypatch.setattr(Server, "sessions", property(_missing_sessions))

    middleware = ReadonlyRetryMiddleware(max_retries=1, base_delay=0.0)
    ctx = _retry_context(tags={TAG_READONLY})

    async def real_call_next(_context: t.Any) -> t.Any:
        return list_sessions(socket_name="retry-skip-smoke")

    with pytest.raises(ToolError):
        asyncio.run(middleware.on_call_tool(ctx, real_call_next))

    assert calls["count"] == 1, (
        f"a missing object was retried (calls={calls['count']}). The skip must "
        f"walk __cause__, because handle_tool_errors wraps it in ExpectedToolError."
    )


def test_readonly_retry_logger_uses_project_namespace() -> None:
    """Retry warnings route through ``libtmux_mcp.retry``, not ``fastmcp.retry``.

    Operators routing logs by the ``libtmux_mcp.*`` namespace prefix
    (matching ``libtmux_mcp.audit``) need retry events to appear on
    the same channel. fastmcp's stock ``RetryMiddleware`` defaults
    to ``fastmcp.retry`` (``error_handling.py:181``); without an
    explicit override, retry warnings would silently bypass any
    project-namespace audit-stream routing.
    """
    middleware = ReadonlyRetryMiddleware()
    assert middleware._retry.logger.name == "libtmux_mcp.retry"


# ---------------------------------------------------------------------------
# ToolErrorResultMiddleware tests
# ---------------------------------------------------------------------------


def _error_probe_server() -> t.Any:
    """Build a minimal FastMCP instance wired like the production stack.

    Only ``ToolErrorResultMiddleware`` is installed — the assertions
    target error-result conversion, not the audit/retry/safety trio.
    Tools mirror the three production raise shapes: an expected
    failure with a chained cause (decorator-mapped libtmux error), an
    expected failure with a recovery suggestion, and an unexpected
    crash.
    """
    from fastmcp import FastMCP
    from libtmux import exc as libtmux_exc

    from libtmux_mcp._utils import ExpectedToolError, _map_exception_to_tool_error
    from libtmux_mcp.middleware import ToolErrorResultMiddleware

    probe = FastMCP(
        name="error-probe",
        middleware=[ToolErrorResultMiddleware(transform_errors=True)],
    )

    @probe.tool
    def fail_expected_chained() -> str:
        # Mirror the ``handle_tool_errors`` decorator's mapping shape:
        # ``raise <mapped ExpectedToolError> from <libtmux exception>``.
        cause = libtmux_exc.PaneNotFound("%99")
        mapped = _map_exception_to_tool_error("fail_expected_chained", cause)
        raise mapped from cause

    @probe.tool
    def fail_expected_with_suggestion() -> str:
        msg = "Pane not found: %99"
        raise ExpectedToolError(
            msg,
            suggestion="Call list_panes to discover valid pane ids.",
        )

    @probe.tool
    def fail_unexpected() -> str:
        msg = "boom"
        raise RuntimeError(msg)

    @probe.tool
    def takes_int(count: int) -> str:
        # Never reached when the argument fails schema validation —
        # fastmcp raises pydantic.ValidationError before tool code runs.
        return str(count)

    return probe


def test_tool_error_result_expected_failure_is_clean() -> None:
    """Expected failures surface verbatim — no transform-layer prefix.

    Regression guard for the stock ``ErrorHandlingMiddleware``
    behavior this middleware replaces: its ``-32603`` catch-all
    rewrote every tool failure to ``"Internal error: <message>"``,
    mangling the agent-facing text.
    """
    from fastmcp import Client

    probe = _error_probe_server()

    async def _call() -> t.Any:
        async with Client(probe) as client:
            return await client.call_tool("fail_expected_chained", raise_on_error=False)

    result = asyncio.run(_call())

    assert result.is_error is True
    text = result.content[0].text
    assert text.startswith("Pane not found:")
    assert "Internal error" not in text


def test_tool_error_result_meta_carries_error_details() -> None:
    """``meta`` reports the originating exception class and expectedness.

    ``error_type`` names the ``__cause__`` when the raise site chained
    one — agents see ``PaneNotFound``, not the mapped
    ``ExpectedToolError`` wrapper.
    """
    from fastmcp import Client

    probe = _error_probe_server()

    async def _call(name: str) -> t.Any:
        async with Client(probe) as client:
            return await client.call_tool(name, raise_on_error=False)

    chained = asyncio.run(_call("fail_expected_chained"))
    assert chained.is_error is True
    meta = chained.meta or {}
    assert meta["error_type"] == "PaneNotFound"
    assert meta["expected"] is True

    crashed = asyncio.run(_call("fail_unexpected"))
    assert crashed.is_error is True
    crash_meta = crashed.meta or {}
    assert crash_meta["error_type"] == "RuntimeError"
    assert crash_meta["expected"] is False


def test_tool_error_result_appends_suggestion() -> None:
    """A ``suggestion`` lands in both the text block and ``meta``."""
    from fastmcp import Client

    probe = _error_probe_server()

    async def _call() -> t.Any:
        async with Client(probe) as client:
            return await client.call_tool(
                "fail_expected_with_suggestion", raise_on_error=False
            )

    result = asyncio.run(_call())

    assert result.is_error is True
    text = result.content[0].text
    assert text == ("Pane not found: %99\nCall list_panes to discover valid pane ids.")
    meta = getattr(result, "meta", None) or {}
    assert meta["suggestion"] == "Call list_panes to discover valid pane ids."


class ErrorLogLevelFixture(t.NamedTuple):
    """Test fixture for ToolErrorResultMiddleware._log_error levels."""

    test_id: str
    tool_name: str
    arguments: dict[str, t.Any] | None
    expected_level: int
    message_fragment: str


ERROR_LOG_LEVEL_FIXTURES: list[ErrorLogLevelFixture] = [
    ErrorLogLevelFixture(
        test_id="expected_failure_logs_warning",
        tool_name="fail_expected_chained",
        arguments=None,
        expected_level=logging.WARNING,
        message_fragment="Pane not found",
    ),
    ErrorLogLevelFixture(
        test_id="unexpected_failure_logs_error",
        tool_name="fail_unexpected",
        arguments=None,
        expected_level=logging.ERROR,
        message_fragment="boom",
    ),
    ErrorLogLevelFixture(
        test_id="schema_validation_logs_warning",
        tool_name="takes_int",
        arguments={"count": "not-an-int"},
        expected_level=logging.WARNING,
        message_fragment="validation error",
    ),
]


@pytest.mark.parametrize(
    ErrorLogLevelFixture._fields,
    ERROR_LOG_LEVEL_FIXTURES,
    ids=[f.test_id for f in ERROR_LOG_LEVEL_FIXTURES],
)
def test_tool_error_result_logs_at_error_log_level(
    test_id: str,
    tool_name: str,
    arguments: dict[str, t.Any] | None,
    expected_level: int,
    message_fragment: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``_log_error`` honors ``log_level``: WARNING expected, ERROR not.

    The stock ``ErrorHandlingMiddleware._log_error`` hardcodes
    ``logger.error`` — without the override, every failure demoted to
    WARNING by ``ExpectedToolError`` would be re-shouted at ERROR on
    the ``fastmcp.errors`` channel. Argument-schema validation
    failures carry no ``log_level`` at all; the middleware classifies
    them as agent-correctable WARNINGs.

    Assertions go through ``record.getMessage()`` so the lazy
    %-formatting args are interpolated regardless of whether a handler
    formatted the record — and a literal ``%s`` leaking into the
    message would fail the fragment match.
    """
    from fastmcp import Client

    # fastmcp's logger is non-propagating (rich handler); pytest >=9.1.0
    # attaches caplog directly to it. ``fastmcp.errors`` records reach
    # caplog by propagating up to ``fastmcp`` — re-enabling propagation
    # to root would then double-capture each emit (``fastmcp`` + root).
    probe = _error_probe_server()

    async def _call() -> None:
        async with Client(probe) as client:
            await client.call_tool(tool_name, arguments, raise_on_error=False)

    with caplog.at_level(logging.DEBUG, logger="fastmcp.errors"):
        asyncio.run(_call())

    levels = {
        r.levelno
        for r in caplog.records
        if r.name == "fastmcp.errors"
        and "Error in tools/call" in r.getMessage()
        and message_fragment in r.getMessage()
    }
    assert levels == {expected_level}


def test_schema_validation_failure_marked_expected_in_meta() -> None:
    """Argument-schema failures report ``expected=True`` in result meta.

    fastmcp validates arguments before tool code runs, so the
    ``handle_tool_errors`` decorators never get the chance to classify
    these as ``ExpectedToolError`` — the middleware must recognize the
    bare ``pydantic.ValidationError`` itself. Bad arguments are
    agent-correctable, same as a bad pane id.
    """
    from fastmcp import Client

    probe = _error_probe_server()

    async def _call() -> t.Any:
        async with Client(probe) as client:
            return await client.call_tool(
                "takes_int", {"count": "not-an-int"}, raise_on_error=False
            )

    result = asyncio.run(_call())

    assert result.is_error is True
    meta = result.meta or {}
    assert meta["expected"] is True
    assert meta["error_type"] == "ValidationError"


class LimiterErrorFixture(t.NamedTuple):
    """Test fixture for error-result preservation through the limiter."""

    test_id: str
    tool_name: str
    arguments: dict[str, t.Any] | None
    expect_is_error: bool
    expect_header: bool
    text_must_contain: str | None


LIMITER_ERROR_FIXTURES: list[LimiterErrorFixture] = [
    LimiterErrorFixture(
        test_id="oversized_error_keeps_flag",
        tool_name="limited_fail",
        arguments={"payload": "x" * 5000},
        expect_is_error=True,
        expect_header=True,
        # Tail-preservation keeps the suggestion line at the end.
        text_must_contain="Call list_panes to discover valid pane ids.",
    ),
    LimiterErrorFixture(
        test_id="small_error_untouched",
        tool_name="limited_fail",
        arguments={"payload": "x"},
        expect_is_error=True,
        expect_header=False,
        text_must_contain="Invalid name: 'x'",
    ),
    LimiterErrorFixture(
        test_id="oversized_success_not_marked_error",
        tool_name="limited_ok",
        arguments=None,
        expect_is_error=False,
        expect_header=True,
        text_must_contain=None,
    ),
]


class LimiterSuccessFixture(t.NamedTuple):
    """Test fixture for schema-bearing successful limiter responses."""

    test_id: str
    payload_size: int


LIMITER_SUCCESS_FIXTURES: list[LimiterSuccessFixture] = [
    LimiterSuccessFixture(
        test_id="schema_success_above_old_backstop",
        payload_size=30_000,
    ),
]


class _LimiterOut(pydantic.BaseModel):
    """Output model giving the ``limited_fail`` probe an output schema.

    Module-level (not test-local) because ``from __future__ import
    annotations`` stringifies the return annotation and pydantic
    resolves it against module globals when fastmcp builds the schema.
    """

    value: str


def _limiter_probe_server(
    *, max_size: int = 300, schema_success_payload_size: int = 0
) -> t.Any:
    """Build a FastMCP instance with the limiter wrapping error conversion.

    Mirrors the production ordering (limiter outside
    ``ToolErrorResultMiddleware``) with a small ``max_size`` so the
    truncation path fires. ``limited_fail`` returns a Pydantic model
    so the MCP SDK client's output-schema validation is in play.
    """
    from fastmcp import FastMCP

    from libtmux_mcp._utils import ExpectedToolError
    from libtmux_mcp.middleware import (
        TailPreservingResponseLimitingMiddleware,
        ToolErrorResultMiddleware,
    )

    probe = FastMCP(
        name="limiter-probe",
        middleware=[
            TailPreservingResponseLimitingMiddleware(
                max_size=max_size,
                tools=["limited_fail", "limited_ok", "limited_model_ok"],
            ),
            ToolErrorResultMiddleware(transform_errors=True),
        ],
    )

    @probe.tool
    def limited_fail(payload: str) -> _LimiterOut:
        msg = f"Invalid name: {payload!r}"
        raise ExpectedToolError(
            msg,
            suggestion="Call list_panes to discover valid pane ids.",
        )

    # output_schema=None: fastmcp wraps even plain-str returns in a
    # result schema, and the stock truncation path drops structured
    # content — the MCP SDK client then rejects ANY truncated success
    # from a schema'd tool. That pre-existing upstream gap is not what
    # this probe tests; disable the schema so the success case
    # exercises only the is_error handling.
    @probe.tool(output_schema=None)
    def limited_ok() -> str:
        return "y" * 5000

    @probe.tool
    def limited_model_ok() -> _LimiterOut:
        return _LimiterOut(value="y" * schema_success_payload_size)

    return probe


@pytest.mark.parametrize(
    LimiterErrorFixture._fields,
    LIMITER_ERROR_FIXTURES,
    ids=[f.test_id for f in LIMITER_ERROR_FIXTURES],
)
def test_response_limiter_preserves_error_results(
    test_id: str,
    tool_name: str,
    arguments: dict[str, t.Any] | None,
    expect_is_error: bool,
    expect_header: bool,
    text_must_contain: str | None,
) -> None:
    """Truncation keeps ``is_error``; successes never gain it.

    Regression guard: the stock truncation path rebuilds the result
    without the error flag, so an oversized error from a tool with an
    output schema became an apparent success — and the MCP SDK client
    raised ``RuntimeError: ... has an output schema but did not return
    structured content`` instead of delivering the tool error. The
    ``limited_fail`` probe returns a Pydantic model precisely so this
    test exercises that client-side validation path.
    """
    from fastmcp import Client

    probe = _limiter_probe_server()

    async def _call() -> t.Any:
        async with Client(probe) as client:
            return await client.call_tool(tool_name, arguments, raise_on_error=False)

    result = asyncio.run(_call())

    assert result.is_error is expect_is_error
    text = result.content[0].text
    assert ("[... truncated" in text) is expect_header
    if text_must_contain is not None:
        assert text_must_contain in text
    if expect_is_error:
        meta = result.meta or {}
        assert meta["expected"] is True
        assert meta["error_type"] == "ExpectedToolError"


@pytest.mark.parametrize(
    LimiterSuccessFixture._fields,
    LIMITER_SUCCESS_FIXTURES,
    ids=[f.test_id for f in LIMITER_SUCCESS_FIXTURES],
)
def test_response_limiter_preserves_schema_success_below_default_backstop(
    test_id: str,
    payload_size: int,
) -> None:
    """Schema-bearing successes below the global backstop stay structured."""
    from fastmcp import Client

    from libtmux_mcp.middleware import DEFAULT_RESPONSE_LIMIT_BYTES

    probe = _limiter_probe_server(
        max_size=DEFAULT_RESPONSE_LIMIT_BYTES,
        schema_success_payload_size=payload_size,
    )

    async def _call() -> t.Any:
        async with Client(probe) as client:
            return await client.call_tool(
                "limited_model_ok",
                raise_on_error=False,
            )

    result = asyncio.run(_call())

    assert test_id
    assert result.structured_content == {"value": "y" * payload_size}


class UnknownArgSuggestionFixture(t.NamedTuple):
    """Test fixture for synthesized unexpected-argument suggestions."""

    test_id: str
    arguments: dict[str, t.Any]
    client_name: str | None
    expect_suggestion: bool
    contains: tuple[str, ...]
    not_contains: tuple[str, ...]


UNKNOWN_ARG_SUGGESTION_FIXTURES: list[UnknownArgSuggestionFixture] = [
    UnknownArgSuggestionFixture(
        test_id="scheduling_flag_names_client",
        arguments={"count": 1, "wait_for_previous": True},
        client_name="gemini-test",
        expect_suggestion=True,
        contains=(
            "Remove or correct the unrecognized argument(s): wait_for_previous.",
            "your client (gemini-test 9.9)",
            "retry the call without it",
        ),
        not_contains=("some clients",),
    ),
    UnknownArgSuggestionFixture(
        test_id="scheduling_flag_default_client",
        arguments={"count": 1, "wait_for_previous": True},
        client_name=None,
        expect_suggestion=True,
        # The in-memory fastmcp Client always sends clientInfo, so the
        # handshake-derived label is used; the no-handshake generic
        # wording is covered by the unit test below.
        contains=("wait_for_previous", "your client ("),
        not_contains=("gemini-test",),
    ),
    UnknownArgSuggestionFixture(
        test_id="typo_lists_names_without_client_note",
        arguments={"count": 1, "bogus_flag": True},
        client_name=None,
        expect_suggestion=True,
        contains=("Remove or correct the unrecognized argument(s): bogus_flag.",),
        not_contains=("scheduling flag", "Gemini"),
    ),
    UnknownArgSuggestionFixture(
        test_id="missing_required_arg_no_suggestion",
        arguments={},
        client_name=None,
        expect_suggestion=False,
        contains=(),
        not_contains=(),
    ),
]


@pytest.mark.parametrize(
    UnknownArgSuggestionFixture._fields,
    UNKNOWN_ARG_SUGGESTION_FIXTURES,
    ids=[f.test_id for f in UNKNOWN_ARG_SUGGESTION_FIXTURES],
)
def test_unexpected_argument_suggestion(
    test_id: str,
    arguments: dict[str, t.Any],
    client_name: str | None,
    expect_suggestion: bool,
    contains: tuple[str, ...],
    not_contains: tuple[str, ...],
) -> None:
    """Schema rejections of unexpected arguments carry a recovery hint.

    The rejection itself is unchanged — the argument is never silently
    stripped (contrast MemPalace/mempalace#322's pop-the-key approach) —
    but the suggestion names exactly which argument(s) to drop or fix,
    and flags ``wait_for_previous`` as a client scheduling leak, naming
    the client from the MCP initialize handshake when available.
    """
    import mcp.types
    from fastmcp import Client

    probe = _error_probe_server()

    client_kwargs: dict[str, t.Any] = {}
    if client_name is not None:
        client_kwargs["client_info"] = mcp.types.Implementation(
            name=client_name, version="9.9"
        )

    async def _call() -> t.Any:
        async with Client(probe, **client_kwargs) as client:
            return await client.call_tool("takes_int", arguments, raise_on_error=False)

    result = asyncio.run(_call())

    assert result.is_error is True
    meta = result.meta or {}
    assert meta["expected"] is True
    if not expect_suggestion:
        assert "suggestion" not in meta
        return
    suggestion = meta["suggestion"]
    text = result.content[0].text
    assert suggestion in text  # appended to the agent-visible message
    for fragment in contains:
        assert fragment in suggestion
    for fragment in not_contains:
        assert fragment not in suggestion


def test_unexpected_argument_suggestion_without_handshake() -> None:
    """No client handshake -> the scheduling-flag note uses generic wording.

    Real connections always carry ``clientInfo`` (required by the MCP
    initialize handshake), so the generic branch is reachable only
    where no context exists — exercised here by calling
    ``_error_tool_result`` directly with a manufactured validation
    error, the same shape fastmcp raises for tool arguments.
    """
    import pydantic

    from libtmux_mcp.middleware import _error_tool_result

    @pydantic.validate_call
    def _probe_fn(count: int) -> int:
        return count  # pragma: no cover - validation rejects the call

    try:
        _probe_fn(count=1, wait_for_previous=True)  # type: ignore[call-arg]
    except pydantic.ValidationError as exc:
        result = _error_tool_result(exc, None)
    else:  # pragma: no cover - defends the test's own premise
        pytest.fail("expected a ValidationError")

    meta = result.meta or {}
    assert meta["expected"] is True
    assert "some clients (e.g. Gemini CLI)" in meta["suggestion"]
