"""Middleware for libtmux MCP server.

Provides three pieces of infrastructure:

* :class:`SafetyMiddleware` gates tools by safety tier based on the
  ``LIBTMUX_SAFETY`` environment variable. Tools tagged above the
  configured tier are hidden from listing and blocked from execution.
* :class:`AuditMiddleware` emits a structured log record for each tool
  invocation (name, duration, outcome, client/request ids, and a
  summary of arguments with payload-bearing fields redacted to a
  length + SHA-256 prefix).
* :class:`TailPreservingResponseLimitingMiddleware` is a backstop cap
  for oversized tool output. Unlike FastMCP's stock
  ``ResponseLimitingMiddleware`` it preserves the **tail** of the
  response — terminal scrollback has its active prompt and the output
  agents actually need at the bottom, so dropping the head is always
  the correct direction.
"""

from __future__ import annotations

import hashlib
import logging
import time
import typing as t

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.error_handling import RetryMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.tools.base import ToolResult
from libtmux import exc as libtmux_exc
from mcp.types import TextContent

from libtmux_mcp._utils import TAG_DESTRUCTIVE, TAG_MUTATING, TAG_READONLY

_TIER_LEVELS: dict[str, int] = {
    TAG_READONLY: 0,
    TAG_MUTATING: 1,
    TAG_DESTRUCTIVE: 2,
}


class SafetyMiddleware(Middleware):
    """Gate tools by safety tier.

    Parameters
    ----------
    max_tier : str
        Maximum allowed tier. One of ``TAG_READONLY``, ``TAG_MUTATING``,
        or ``TAG_DESTRUCTIVE``.
    """

    def __init__(self, max_tier: str = TAG_MUTATING) -> None:
        self.max_level = _TIER_LEVELS.get(max_tier, 1)

    def _is_allowed(self, tags: set[str]) -> bool:
        """Return True if the tool's tags fall within the allowed tier.

        Fail-closed: tools without a recognized tier tag are denied.
        """
        found_tier = False
        for tier, level in _TIER_LEVELS.items():
            if tier in tags:
                found_tier = True
                if level > self.max_level:
                    return False
        return found_tier

    async def on_list_tools(
        self,
        context: MiddlewareContext,
        call_next: t.Any,
    ) -> t.Any:
        """Filter tools above the safety tier from the listing."""
        tools = await call_next(context)
        return [tool for tool in tools if self._is_allowed(tool.tags)]

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: t.Any,
    ) -> t.Any:
        """Block execution of tools above the safety tier."""
        if context.fastmcp_context:
            tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
            if tool and not self._is_allowed(tool.tags):
                msg = (
                    f"Tool '{context.message.name}' is not available at the "
                    f"current safety level. Set LIBTMUX_SAFETY=destructive "
                    f"to enable destructive tools."
                )
                raise ToolError(msg)
        return await call_next(context)


# ---------------------------------------------------------------------------
# Audit middleware
# ---------------------------------------------------------------------------

#: Argument names that carry user-supplied payloads we never want in logs.
#: ``keys`` (send_keys), ``text`` (paste_text), ``value`` (set_environment),
#: ``content`` (load_buffer), and ``shell`` (respawn_pane) can contain
#: commands, secrets, or arbitrary large strings. Matched by exact name,
#: case-sensitive, to mirror the tool signatures.
#:
#: Note on ``shell`` redaction: this redacts the MCP audit log only.
#: ``respawn_pane(shell="env SECRET=... bash")`` may briefly expose the
#: argument via the OS process table and tmux's ``pane_current_command``
#: metadata until the spawned shell takes over — see ``docs/topics/safety.md``.
_SENSITIVE_ARG_NAMES: frozenset[str] = frozenset(
    {"keys", "text", "value", "content", "shell"}
)

#: String arguments longer than this get truncated in the log summary to
#: keep records bounded. Non-sensitive strings only — sensitive ones are
#: replaced entirely by their digest.
_MAX_LOGGED_STR_LEN: int = 200


def _redact_digest(value: str) -> dict[str, t.Any]:
    """Return a length + SHA-256 prefix summary of ``value``.

    The digest is stable and deterministic, which lets operators
    correlate the same payload across log lines without ever recording
    the payload itself.

    Examples
    --------
    >>> _redact_digest("hello")
    {'len': 5, 'sha256_prefix': '2cf24dba5fb0'}
    >>> _redact_digest("")
    {'len': 0, 'sha256_prefix': 'e3b0c44298fc'}
    """
    return {
        "len": len(value),
        "sha256_prefix": hashlib.sha256(value.encode("utf-8")).hexdigest()[:12],
    }


def _summarize_args(args: dict[str, t.Any]) -> dict[str, t.Any]:
    """Summarize tool arguments for audit logging.

    Sensitive keys get replaced by a digest; over-long strings get
    truncated with a marker; everything else passes through as-is.

    Examples
    --------
    Non-sensitive scalars pass through unchanged:

    >>> _summarize_args({"pane_id": "%1", "bracket": True})
    {'pane_id': '%1', 'bracket': True}

    Sensitive payload names are replaced by a digest dict:

    >>> _summarize_args({"keys": "rm -rf /"})["keys"]["len"]
    8
    """
    summary: dict[str, t.Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and key in _SENSITIVE_ARG_NAMES:
            summary[key] = _redact_digest(value)
        elif isinstance(value, str) and len(value) > _MAX_LOGGED_STR_LEN:
            summary[key] = value[:_MAX_LOGGED_STR_LEN] + "...<truncated>"
        else:
            summary[key] = value
    return summary


class AuditMiddleware(Middleware):
    """Emit a structured log record per tool invocation.

    Records carry: tool name, outcome (ok/error), duration in ms,
    error type on failure, the fastmcp client_id / request_id when
    available, and a redacted argument summary. The logger name is
    ``libtmux_mcp.audit`` by default so operators can route it
    independently (e.g. to a JSON file) without touching the module
    ``libtmux_mcp`` logger.

    Parameters
    ----------
    logger_name : str
        Name of the :mod:`logging` logger used for audit records.
    """

    def __init__(self, logger_name: str = "libtmux_mcp.audit") -> None:
        self._logger = logging.getLogger(logger_name)

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: t.Any,
    ) -> t.Any:
        """Wrap the tool call with a timer and emit one audit record."""
        start = time.monotonic()
        tool_name = getattr(context.message, "name", "<unknown>")
        raw_args = getattr(context.message, "arguments", None) or {}
        args_summary = _summarize_args(raw_args)

        client_id: str | None = None
        request_id: str | None = None
        if context.fastmcp_context is not None:
            client_id = getattr(context.fastmcp_context, "client_id", None)
            request_id = getattr(context.fastmcp_context, "request_id", None)

        try:
            result = await call_next(context)
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000.0
            self._logger.info(
                "tool=%s outcome=error error_type=%s duration_ms=%.2f "
                "client_id=%s request_id=%s args=%s",
                tool_name,
                type(exc).__name__,
                duration_ms,
                client_id,
                request_id,
                args_summary,
            )
            raise

        duration_ms = (time.monotonic() - start) * 1000.0
        self._logger.info(
            "tool=%s outcome=ok duration_ms=%.2f client_id=%s request_id=%s args=%s",
            tool_name,
            duration_ms,
            client_id,
            request_id,
            args_summary,
        )
        return result


# ---------------------------------------------------------------------------
# Tail-preserving response limiter
# ---------------------------------------------------------------------------

#: Default byte ceiling for :class:`TailPreservingResponseLimitingMiddleware`.
#: Chosen strictly above the per-tool ``max_lines`` caps (500 lines x
#: ~100 bytes/line) so normal operation does not trip the middleware —
#: it only fires when a tool forgot to declare its own cap or the user
#: opted out via ``max_lines=None``.
DEFAULT_RESPONSE_LIMIT_BYTES = 50_000


class ReadonlyRetryMiddleware(Middleware):
    """Retry transient libtmux failures, but only for readonly tools.

    Wraps fastmcp's :class:`fastmcp.server.middleware.error_handling.RetryMiddleware`
    so retries are bounded by the safety tier the tool is registered
    under. Mutating and destructive tools (``send_keys``,
    ``create_session``, ``kill_server``, …) pass straight through —
    re-running them on a transient socket error would silently double
    side effects, which is unacceptable. Readonly tools
    (``list_sessions``, ``capture_pane``, ``snapshot_pane``, …) are
    safe to retry because they observe state without mutating it.

    Default retry trigger is :class:`libtmux.exc.LibTmuxException` —
    libtmux wraps the subprocess failures we actually want to retry
    (socket EAGAIN, transient connect errors). The fastmcp default
    ``(ConnectionError, TimeoutError)`` does NOT match these, so the
    upstream defaults would be a silent no-op.

    Place this in the middleware stack **inside** ``AuditMiddleware``
    (so retried calls are audited once each) and **outside**
    ``SafetyMiddleware`` (so tier-denied tools never reach retry).
    """

    def __init__(
        self,
        max_retries: int = 1,
        base_delay: float = 0.1,
        max_delay: float = 1.0,
        backoff_multiplier: float = 2.0,
        retry_exceptions: tuple[type[Exception], ...] = (libtmux_exc.LibTmuxException,),
        logger_: logging.Logger | None = None,
    ) -> None:
        """Configure the underlying retry policy.

        Defaults are deliberately small. ``max_retries=1`` keeps audit
        log noise minimal (one retry per failed call, not three). The
        100 ms / 1 s backoff window matches the expected duration of a
        transient libtmux socket hiccup — a longer backoff would just
        delay a real failure without adding meaningful retry headroom.

        ``logger_`` defaults to ``logging.getLogger("libtmux_mcp.retry")``
        when not supplied — keeps retry events on the project's
        ``libtmux_mcp.*`` namespace so operators routing the audit
        stream capture them. Without this default, fastmcp's stock
        ``RetryMiddleware`` would log to ``fastmcp.retry`` and miss
        any project-namespace log routing.
        """
        if logger_ is None:
            logger_ = logging.getLogger("libtmux_mcp.retry")
        self._retry = RetryMiddleware(
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            backoff_multiplier=backoff_multiplier,
            retry_exceptions=retry_exceptions,
            logger=logger_,
        )

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: t.Any,
    ) -> t.Any:
        """Delegate to the upstream retry only for tools tagged readonly."""
        if context.fastmcp_context:
            tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
            if tool and TAG_READONLY in tool.tags:
                return await self._retry.on_request(context, call_next)
        return await call_next(context)


#: Header prefixed to a truncated response. Intentionally matches the
#: format used by the per-tool ``capture_pane`` truncation so clients
#: see a consistent marker regardless of which layer fired.
_TRUNCATION_HEADER_TEMPLATE = "[... truncated {dropped} bytes ...]\n"


class TailPreservingResponseLimitingMiddleware(ResponseLimitingMiddleware):
    """Response-limiter that keeps the tail of oversized output.

    FastMCP's stock :class:`ResponseLimitingMiddleware` truncates the
    tail of the response (keeps the start, appends a suffix). That's
    exactly wrong for terminal scrollback, where the active shell
    prompt and most recent command output live at the **bottom** of
    the buffer. This subclass overrides ``_truncate_to_result`` to
    drop the head instead, prefixing a single truncation-header line
    so callers can detect the cap fired.

    Used as a global backstop for :func:`libtmux_mcp.tools.pane_tools.io.capture_pane`,
    :func:`libtmux_mcp.tools.pane_tools.meta.snapshot_pane`, and
    :func:`libtmux_mcp.tools.pane_tools.search.search_panes`. Per-tool
    caps at the tool layer fire first under normal operation; this
    middleware catches pathological output from future tools that
    forget to declare their own bounds.
    """

    def _truncate_to_result(
        self,
        text: str,
        meta: dict[str, t.Any] | None = None,
    ) -> ToolResult:
        """Keep the last ``max_size`` bytes of ``text`` and prefix a header.

        Overrides the base class implementation, which keeps the head.
        """
        encoded = text.encode("utf-8")
        if len(encoded) <= self.max_size:
            # Shouldn't normally happen — the caller already decided
            # we were over limit — but be defensive.
            return ToolResult(
                content=[TextContent(type="text", text=text)],
                meta=meta if meta is not None else {},
            )

        # Reserve space for the truncation header. The header length
        # depends on the number of dropped bytes, which in turn
        # depends on how much we keep — so compute conservatively
        # (worst-case integer width) then re-trim.
        header = _TRUNCATION_HEADER_TEMPLATE.format(dropped=len(encoded))
        header_bytes = len(header.encode("utf-8"))
        # JSON-wrapper overhead mirrors the base class accounting.
        overhead = 50
        target_size = self.max_size - header_bytes - overhead
        if target_size <= 0:
            return ToolResult(
                content=[TextContent(type="text", text=header.rstrip("\n"))],
                meta=meta if meta is not None else {},
            )

        # Take the LAST ``target_size`` bytes. Decode with errors=ignore
        # so a split UTF-8 sequence at the boundary is dropped rather
        # than corrupting the output.
        tail = encoded[-target_size:].decode("utf-8", errors="ignore")
        dropped = len(encoded) - len(tail.encode("utf-8"))
        final_header = _TRUNCATION_HEADER_TEMPLATE.format(dropped=dropped)
        truncated = final_header + tail
        return ToolResult(
            content=[TextContent(type="text", text=truncated)],
            meta=meta if meta is not None else {},
        )
