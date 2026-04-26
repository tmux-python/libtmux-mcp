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
        "content": "buffer payload",
        "shell": "psql -U user -W secret123 mydb",
        "pane_id": "%1",
        "bracket": True,
    }
    summary = _summarize_args(args)
    for sensitive in ("keys", "text", "value", "content", "shell"):
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

    The ordering is load-bearing (see server.py comment):
    TimingMiddleware must be outermost so it observes total wall
    time; AuditMiddleware must sit *outside* SafetyMiddleware so
    tier-denial events (which raise ``ToolError`` before
    ``call_next``) are still recorded — without this ordering,
    forbidden-access attempts silently bypass the audit log. A
    refactor that swaps Audit and Safety would degrade
    security observability without an obvious test failure, so pin
    the sequence explicitly.

    ReadonlyRetryMiddleware sits between Audit and Safety so retried
    calls are audited once each (Audit wraps the retry loop) and
    tier-denied tools never reach retry (Safety stops them first).
    """
    from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
    from fastmcp.server.middleware.timing import TimingMiddleware

    from libtmux_mcp.middleware import (
        AuditMiddleware,
        ReadonlyRetryMiddleware,
        SafetyMiddleware,
        TailPreservingResponseLimitingMiddleware,
    )
    from libtmux_mcp.server import mcp

    types = [type(mw) for mw in mcp.middleware]
    # FastMCP auto-appends an internal DereferenceRefsMiddleware at the
    # end of the stack; we care about the ordering of the middleware
    # *we* configured. Slice off the suffix before comparing.
    assert types[:6] == [
        TimingMiddleware,
        TailPreservingResponseLimitingMiddleware,
        ErrorHandlingMiddleware,
        AuditMiddleware,
        ReadonlyRetryMiddleware,
        SafetyMiddleware,
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


def test_audit_records_safety_denial(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tool denied by SafetyMiddleware still appears in the audit log.

    Composes Audit and Safety in the production order (Audit outside
    Safety) by manually nesting their ``on_call_tool`` handlers: the
    inner ``call_next`` from Audit dispatches to Safety, which raises
    ``ToolError`` for an over-tier tool. Audit should record that as
    ``outcome=error error_type=ToolError`` rather than skipping the
    record. Without this ordering, denied access attempts would
    silently bypass forensic logging.
    """
    from fastmcp.exceptions import ToolError

    audit = AuditMiddleware()
    ctx = _fake_context(name="kill_server", arguments={})

    # SafetyMiddleware.on_call_tool consults
    # context.fastmcp_context.fastmcp.get_tool(...). With
    # fastmcp_context=None the safety check short-circuits, so we
    # simulate the denial more directly: ``call_next`` is a coroutine
    # that raises the same ``ToolError`` SafetyMiddleware would when
    # blocking an over-tier call. The test's invariant is that the
    # AuditMiddleware sitting *outside* Safety still records the
    # attempt with outcome=error.
    msg = "Tool 'kill_server' is not available at the current safety level."

    async def _safety_denial(_ctx: t.Any) -> None:
        raise ToolError(msg)

    with (
        caplog.at_level(logging.INFO, logger="libtmux_mcp.audit"),
        pytest.raises(ToolError, match="not available"),
    ):
        asyncio.run(audit.on_call_tool(ctx, _safety_denial))

    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "tool=kill_server" in rendered
    assert "outcome=error" in rendered
    assert "error_type=ToolError" in rendered


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
    the retry the agent would see a ``ToolError`` on the first
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
    ``handle_tool_errors_async`` (``_utils.py:842-850``), which
    converts ``LibTmuxException`` to ``ToolError(...) from
    LibTmuxException``. At the middleware layer the exception type
    is ``ToolError``, not ``LibTmuxException`` — so the retry
    decision must walk ``__cause__`` to see the real failure type.

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
