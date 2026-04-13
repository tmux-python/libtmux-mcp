"""Middleware for libtmux MCP server.

Provides two pieces of infrastructure:

* :class:`SafetyMiddleware` gates tools by safety tier based on the
  ``LIBTMUX_SAFETY`` environment variable. Tools tagged above the
  configured tier are hidden from listing and blocked from execution.
* :class:`AuditMiddleware` emits a structured log record for each tool
  invocation (name, duration, outcome, client/request ids, and a
  summary of arguments with payload-bearing fields redacted to a
  length + SHA-256 prefix).
"""

from __future__ import annotations

import hashlib
import logging
import time
import typing as t

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

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
#: ``keys`` (send_keys), ``text`` (paste_text), and ``value`` (set_environment)
#: can contain commands, secrets, or arbitrary large strings. Matched by
#: exact name, case-sensitive, to mirror the tool signatures.
_SENSITIVE_ARG_NAMES: frozenset[str] = frozenset({"keys", "text", "value"})

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
