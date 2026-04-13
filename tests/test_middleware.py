"""Tests for libtmux MCP safety + audit middleware."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import typing as t
from types import SimpleNamespace

import pytest

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
    args = {
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
        assert args[sensitive] not in str(summary[sensitive])
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
) -> SimpleNamespace:
    """Build a MiddlewareContext-like stub for audit tests."""
    message = SimpleNamespace(name=name, arguments=arguments or {})
    fastmcp_context = SimpleNamespace(
        client_id="test-client",
        request_id="req-42",
    )
    return SimpleNamespace(message=message, fastmcp_context=fastmcp_context)


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
    assert any("client_id=test-client" in m for m in messages)


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
