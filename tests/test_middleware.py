"""Tests for libtmux MCP safety + audit middleware."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import typing as t

import pytest
from fastmcp.server.middleware import MiddlewareContext
from mcp.types import CallToolRequestParams

from libtmux_mcp._utils import TAG_DESTRUCTIVE, TAG_MUTATING, TAG_READONLY
from libtmux_mcp.middleware import (
    AuditMiddleware,
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
    """SafetyMiddleware falls back to mutating for unknown tiers."""
    mw = SafetyMiddleware(max_tier="nonexistent")
    assert mw._is_allowed({TAG_READONLY}) is True
    assert mw._is_allowed({TAG_MUTATING}) is True
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
        "value": "supersecret",
        "pane_id": "%1",
        "bracket": True,
    }
    summary = _summarize_args(args)
    for sensitive in ("keys", "text", "value"):
        assert isinstance(summary[sensitive], dict)
        assert "len" in summary[sensitive]
        assert "sha256_prefix" in summary[sensitive]
        raw_value = args[sensitive]
        assert isinstance(raw_value, str)
        assert raw_value not in str(summary[sensitive])
    # Non-sensitive args pass through unchanged.
    assert summary["pane_id"] == "%1"
    assert summary["bracket"] is True


def test_summarize_args_truncates_long_non_sensitive_strings() -> None:
    """Non-sensitive strings over the cap get truncated with a marker."""
    args = {"output_path": "x" * 500}
    summary = _summarize_args(args)
    assert summary["output_path"].endswith("...<truncated>")
    assert len(summary["output_path"]) < 500


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

    The ordering is load-bearing (see server.py comment): TimingMiddleware
    must be outermost so it observes total wall time; AuditMiddleware
    must be innermost so it logs the call/args directly adjacent to the
    tool invocation. A refactor that accidentally reorders these would
    degrade observability without an obvious failure mode, so pin the
    sequence explicitly.
    """
    from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
    from fastmcp.server.middleware.timing import TimingMiddleware

    from libtmux_mcp.middleware import (
        AuditMiddleware,
        SafetyMiddleware,
        TailPreservingResponseLimitingMiddleware,
    )
    from libtmux_mcp.server import mcp

    types = [type(mw) for mw in mcp.middleware]
    # FastMCP auto-appends an internal DereferenceRefsMiddleware at the
    # end of the stack; we care about the ordering of the middleware
    # *we* configured. Slice off the suffix before comparing.
    assert types[:5] == [
        TimingMiddleware,
        TailPreservingResponseLimitingMiddleware,
        ErrorHandlingMiddleware,
        SafetyMiddleware,
        AuditMiddleware,
    ]


def test_error_handling_middleware_transforms_errors() -> None:
    """ErrorHandlingMiddleware is configured with transform_errors=True.

    Regression guard: without ``transform_errors=True`` the middleware
    would still log but not map resource errors to MCP error code
    ``-32002``, which is the protocol-correctness point of adopting
    this middleware in the first place.
    """
    from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware

    from libtmux_mcp.server import mcp

    err_mw = next(
        mw for mw in mcp.middleware if isinstance(mw, ErrorHandlingMiddleware)
    )
    assert err_mw.transform_errors is True
