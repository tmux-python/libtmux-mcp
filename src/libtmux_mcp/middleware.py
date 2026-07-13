"""Middleware for libtmux MCP server.

Provides the project's middleware infrastructure, in definition order:

* :class:`SafetyMiddleware` gates tools by safety tier based on the
  ``LIBTMUX_SAFETY`` environment variable. Tools tagged above the
  configured tier are hidden from listing and blocked from execution.
* :class:`ToolErrorResultMiddleware` converts tool-call failures into
  ``ToolResult(is_error=True)`` results that carry the clean error
  message plus a structured ``meta`` payload, instead of fastmcp's
  stock ``-32603`` catch-all that prefixed every expected failure
  with ``"Internal error: "``.
* :class:`AuditMiddleware` emits a structured log record for each tool
  invocation (name, duration, outcome, client/request ids, and a
  summary of arguments with payload-bearing fields redacted to a
  length + SHA-256 prefix).
* :class:`ReadonlyRetryMiddleware` retries transient libtmux failures,
  but only for readonly tools — re-running a mutating tool would
  silently double side effects.
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

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.error_handling import (
    ErrorHandlingMiddleware,
    RetryMiddleware,
)
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.tools.base import ToolResult
from libtmux import exc as libtmux_exc
from mcp.types import CallToolRequestParams, TextContent
from pydantic import ValidationError as PydanticValidationError

from libtmux_mcp._utils import (
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    ExpectedToolError,
)

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
        self.max_level = _TIER_LEVELS.get(max_tier, 0)

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
                raise ExpectedToolError(msg)
        return await call_next(context)


# ---------------------------------------------------------------------------
# Tool-error result conversion
# ---------------------------------------------------------------------------


def _schema_validation_error(
    error: BaseException,
) -> PydanticValidationError | None:
    """Return the Pydantic validation error behind a schema failure."""
    if isinstance(error, PydanticValidationError):
        return error
    cause = error.__cause__
    if isinstance(cause, PydanticValidationError):
        return cause
    return None


def _is_schema_validation_error(error: BaseException) -> bool:
    """Return True for fastmcp argument-schema validation failures.

    fastmcp validates tool arguments against the input schema *before*
    tool code runs, raising a bare :exc:`pydantic.ValidationError` —
    too early for the ``handle_tool_errors`` decorators to classify.
    Bad arguments are agent-correctable (fix the call and retry), so
    they get the same expected/WARNING treatment as
    :class:`~libtmux_mcp._utils.ExpectedToolError`.

    Output validation cannot be mistaken for this case: fastmcp's tool
    layer converts output-shape failures into error results itself, so
    they never reach the middleware as exceptions.
    """
    return _schema_validation_error(error) is not None


def _validation_errors_without_inputs(
    error: PydanticValidationError,
) -> list[dict[str, t.Any]]:
    """Return validation errors without rejected input values."""
    return t.cast(
        "list[dict[str, t.Any]]",
        error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        ),
    )


def _format_schema_validation_error(error: BaseException) -> str:
    """Format a Pydantic validation error without raw input values."""
    err = _schema_validation_error(error)
    if err is None:
        return str(error)
    count = err.error_count()
    noun = "validation error" if count == 1 else "validation errors"
    lines = [f"{count} {noun} for {err.title}"]
    for item in _validation_errors_without_inputs(err):
        loc = ".".join(str(part) for part in item.get("loc", ())) or "__root__"
        msg = str(item.get("msg", "Input validation failed"))
        error_type = str(item.get("type", "unknown"))
        lines.extend((loc, f"  {msg} [type={error_type}]"))
    return "\n".join(lines)


def _strip_validation_error_inputs(value: t.Any) -> t.Any:
    """Remove raw input payloads from structured validation errors."""
    if isinstance(value, dict):
        return {
            key: _strip_validation_error_inputs(item)
            for key, item in value.items()
            if key not in {"ctx", "input"}
        }
    if isinstance(value, list):
        return [_strip_validation_error_inputs(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_validation_error_inputs(item) for item in value)
    return value


class _FastMCPValidationLogFilter(logging.Filter):
    """Redact FastMCP invalid-argument warning payloads."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg != "Invalid arguments for tool %r: %s":
            return True
        if not isinstance(record.args, tuple) or len(record.args) != 2:
            return True
        tool_name, errors = record.args
        record.args = (tool_name, _strip_validation_error_inputs(errors))
        return True


def install_fastmcp_validation_log_filter() -> None:
    """Install the FastMCP validation log redaction filter once."""
    logger = logging.getLogger("fastmcp.server.server")
    if not any(
        isinstance(item, _FastMCPValidationLogFilter) for item in logger.filters
    ):
        logger.addFilter(_FastMCPValidationLogFilter())


#: Scheduling flag some MCP clients (notably Gemini CLI when batching
#: several tool calls in one turn) merge into the tool's arguments.
#: Recognized only to *word the rejection helpfully* — the argument is
#: still rejected, never silently stripped, so genuine argument typos
#: from other clients stay loud. Contrast MemPalace/mempalace#322,
#: which strips the key, and #647, which whitelists arguments against
#: the schema — silent dropping would let a mis-named flag on a
#: mutating tool (e.g. ``enter`` on send_keys) run with defaults.
_CLIENT_SCHEDULING_FLAG = "wait_for_previous"


def _unexpected_kwargs(error: BaseException) -> list[str]:
    """Argument names rejected as unexpected by schema validation.

    Reads pydantic's structured ``errors()`` from ``error`` or its
    ``__cause__`` (same posture as :func:`_is_schema_validation_error`)
    and returns the names flagged ``unexpected_keyword_argument``.
    Empty list for every other failure shape.
    """
    err = _schema_validation_error(error)
    if err is None:
        return []
    return [
        str(item["loc"][-1])
        for item in err.errors()
        if item.get("type") == "unexpected_keyword_argument" and item.get("loc")
    ]


def _client_label(context: MiddlewareContext | None) -> str | None:
    """``"name version"`` of the connected client, when the handshake exposed it.

    Walks ``fastmcp_context.session.client_params.clientInfo`` — the
    MCP ``initialize`` handshake's client identity. Every hop can be
    absent (unit-test contexts, background tasks, clients that omit
    ``clientInfo``), so any failure resolves to ``None``. Used only to
    word error suggestions; never gates behavior.
    """
    if context is None:
        return None
    try:
        fastmcp_ctx = context.fastmcp_context
        if fastmcp_ctx is None:
            return None
        params = fastmcp_ctx.session.client_params
        if params is None:
            return None
        info = params.clientInfo
        return f"{info.name} {info.version}".strip()
    except (AttributeError, RuntimeError):
        return None


def _error_tool_result(
    error: Exception,
    context: MiddlewareContext | None = None,
) -> ToolResult:
    """Build a rich ``ToolResult(is_error=True)`` from a tool failure.

    The text block carries the error message exactly as raised — no
    transform-layer prefix — with the recovery ``suggestion`` appended
    when the error provides one. ``meta`` mirrors the details in
    machine-readable form:

    * ``error_type`` — class name of the originating exception
      (``__cause__`` when the raise site chained one, so agents see
      ``PaneNotFound`` rather than the ``ToolError`` wrapper).
    * ``expected`` — True for agent-correctable failures
      (:class:`~libtmux_mcp._utils.ExpectedToolError` and
      argument-schema validation errors), False for operator faults
      and potential server bugs.
    * ``suggestion`` — recovery hint. Carried by the error when the
      raise site provided one; synthesized for schema-validation
      failures that rejected unexpected arguments, so the agent knows
      to drop or fix exactly those names (with a client-flag note for
      :data:`_CLIENT_SCHEDULING_FLAG` leaks, naming the client via
      ``context`` when the handshake exposed it).

    ``structured_content`` is deliberately left unset: tools declare
    output schemas for their success payloads, and clients validate
    ``structuredContent`` against them — an error-shaped payload there
    would fail validation on strict clients.
    """
    cause = error.__cause__
    origin = cause if cause is not None else error
    meta: dict[str, t.Any] = {
        "error_type": type(origin).__name__,
        "expected": isinstance(error, ExpectedToolError)
        or _is_schema_validation_error(error),
    }
    text = (
        _format_schema_validation_error(error)
        if _is_schema_validation_error(error)
        else str(error)
    )
    suggestion = getattr(error, "suggestion", None)
    if suggestion is None:
        unknown = _unexpected_kwargs(error)
        if unknown:
            suggestion = (
                f"Remove or correct the unrecognized argument(s): {', '.join(unknown)}."
            )
            if _CLIENT_SCHEDULING_FLAG in unknown:
                client = _client_label(context)
                who = (
                    f"your client ({client})"
                    if client
                    else "some clients (e.g. Gemini CLI)"
                )
                suggestion += (
                    f" {_CLIENT_SCHEDULING_FLAG} is a scheduling flag "
                    f"{who} can leak into batched tool calls; retry "
                    f"the call without it."
                )
    if suggestion:
        meta["suggestion"] = suggestion
        text = f"{text}\n{suggestion}"
    return ToolResult(
        content=[TextContent(type="text", text=text)],
        meta=meta,
        is_error=True,
    )


class ToolErrorResultMiddleware(ErrorHandlingMiddleware):
    """Convert tool-call failures into rich ``ToolResult`` errors.

    Replaces the stock :class:`ErrorHandlingMiddleware` behavior for
    ``tools/call`` only. The stock ``transform_errors=True`` path
    funnels every non-MCP exception through a ``-32603`` catch-all, so
    agents received ``"Internal error: Pane not found: %5"`` — the
    transform mangled every expected failure message. This subclass
    intercepts tool-call exceptions first (``on_call_tool`` is the
    innermost hook of a middleware's chain) and returns
    :func:`_error_tool_result` instead; non-tool messages fall through
    to the inherited ``on_message``, preserving the MCP ``-32002``
    resource-not-found transform this middleware was originally
    adopted for.

    Logging honors ``FastMCPError.log_level`` (fastmcp >= 3.3): the
    expected failures demoted to WARNING by
    :class:`~libtmux_mcp._utils.ExpectedToolError` no longer get
    re-shouted at ERROR by the stock ``_log_error``. Argument-schema
    validation failures — raised by fastmcp before tool code can
    classify them — are treated as expected too (see
    :func:`_is_schema_validation_error`), and when the rejected
    arguments include unexpected names the error result carries a
    synthesized suggestion telling the agent which names to drop or
    fix (see :func:`_error_tool_result`).

    Ordering invariant: must sit **outside** ``AuditMiddleware``,
    ``ReadonlyRetryMiddleware``, and ``SafetyMiddleware``. All three
    depend on exception semantics — audit detects failures by catching,
    retry matches ``LibTmuxException`` via ``__cause__``, and safety's
    tier denials must propagate as exceptions for audit to record them
    — so converting the exception to a result any deeper in the stack
    would silently break all three.
    """

    def _log_error(self, error: Exception, context: MiddlewareContext) -> None:
        """Log at the error's own ``log_level`` instead of a flat ERROR.

        Mirrors the stock implementation (error-count tracking,
        optional traceback, error callback) but routes the record
        through ``logger.log`` with ``FastMCPError.log_level``.
        Exceptions that don't carry one default to ERROR — except
        argument-schema validation failures, which are
        agent-correctable and log at WARNING like every other invalid
        argument (see :func:`_is_schema_validation_error`).
        """
        level: int | None = getattr(error, "log_level", None)
        if level is None:
            level = (
                logging.WARNING if _is_schema_validation_error(error) else logging.ERROR
            )

        error_type = type(error).__name__
        method = context.method or "unknown"

        error_key = f"{error_type}:{method}"
        self.error_counts[error_key] = self.error_counts.get(error_key, 0) + 1

        # Lazy %-formatting (project logging standard) — also collapses
        # the stock implementation's include_traceback branch, since
        # ``exc_info`` accepts a bool.
        error_text = (
            _format_schema_validation_error(error)
            if _is_schema_validation_error(error)
            else str(error)
        )
        self.logger.log(
            level,
            "Error in %s: %s: %s",
            method,
            error_type,
            error_text,
            exc_info=self.include_traceback,
        )

        if self.error_callback:
            try:
                self.error_callback(error, context)
            except Exception:
                self.logger.exception("Error in error callback")

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: t.Any,
    ) -> t.Any:
        """Convert tool-call exceptions into ``is_error`` results."""
        try:
            return await call_next(context)
        except Exception as error:
            self._log_error(error, context)
            return _error_tool_result(error, context)


# ---------------------------------------------------------------------------
# Audit middleware
# ---------------------------------------------------------------------------

#: Argument names that carry user-supplied payloads we never want in logs.
#: ``keys`` (send_keys), ``text`` (paste_text), ``command`` (run_command),
#: ``value`` (set_environment), ``content`` (load_buffer), ``shell``
#: (respawn_pane), and ``environment`` (respawn_pane) can contain commands,
#: secrets, or arbitrary large strings.
#: Matched by exact name, case-sensitive, to mirror the tool signatures.
#:
#: ``environment`` accepts a mapping or a JSON object string. The redaction
#: logic in :func:`_summarize_args` digests each mapping *value* while leaving
#: its *keys* (env var names like ``DATABASE_URL``) visible. A JSON string is
#: instead redacted as one scalar digest, so its keys are not retained.
#:
#: Note on ``shell`` and ``environment`` redaction: this redacts the MCP
#: audit log only. ``respawn_pane(shell="env SECRET=... bash")`` and
#: ``environment={"AWS_SECRET_KEY": "..."}`` may briefly expose the values
#: via the OS process table and tmux's ``pane_current_command`` metadata
#: until the spawned shell takes over — see ``docs/topics/safety.md``.
_SENSITIVE_ARG_NAMES: frozenset[str] = frozenset(
    {"keys", "text", "command", "value", "content", "shell", "environment"}
)

#: Nested argument containers that may contain sensitive argument names.
#: ``operations`` is used by ``send_keys_batch`` and the generic tool-batch
#: wrappers. Preserving routing metadata is useful for audit trails, but
#: nested payloads must be digested the same way top-level tool calls are.
_NESTED_ARG_LIST_NAMES: frozenset[str] = frozenset({"operations"})

_NONE_TYPE = type(None)

_SEND_KEYS_OPERATION_ARG_TYPES: dict[str, tuple[type[t.Any], ...]] = {
    "keys": (str,),
    "pane_id": (str, _NONE_TYPE),
    "session_name": (str, _NONE_TYPE),
    "session_id": (str, _NONE_TYPE),
    "window_id": (str, _NONE_TYPE),
    "enter": (bool,),
    "literal": (bool,),
    "suppress_history": (bool,),
}

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


def _redacted_value_shape(value: t.Any) -> dict[str, t.Any]:
    """Return non-payload metadata for a value that cannot be logged."""
    return {"type": type(value).__name__, "redacted": True}


def _summarize_send_keys_operation_args(args: dict[str, t.Any]) -> dict[str, t.Any]:
    """Summarize one ``send_keys_batch`` operation for audit logging."""
    summary: dict[str, t.Any] = {}
    for key, value in args.items():
        expected_types = _SEND_KEYS_OPERATION_ARG_TYPES.get(key)
        if expected_types is None or not isinstance(value, expected_types):
            summary[key] = _redacted_value_shape(value)
        else:
            summary[key] = _summarize_args({key: value})[key]
    return summary


def _summarize_tool_batch_operation_args(args: dict[str, t.Any]) -> dict[str, t.Any]:
    """Summarize one generic tool-batch operation for audit logging."""
    summary: dict[str, t.Any] = {}
    for key, value in args.items():
        if key == "tool" and isinstance(value, str):
            summary[key] = value
        elif key == "arguments" and isinstance(value, dict):
            summary[key] = _summarize_args(value)
        else:
            summary[key] = _redacted_value_shape(value)
    return summary


def _summarize_nested_operation_args(args: dict[str, t.Any]) -> dict[str, t.Any]:
    """Summarize a known nested operation shape."""
    if "tool" in args or "arguments" in args:
        return _summarize_tool_batch_operation_args(args)
    return _summarize_send_keys_operation_args(args)


def _summarize_args(args: dict[str, t.Any]) -> dict[str, t.Any]:
    """Summarize tool arguments for audit logging.

    Sensitive keys get replaced by a digest; over-long strings get
    truncated with a marker; everything else passes through as-is.
    Sensitive values that are dict-shaped (e.g. ``environment`` on
    ``respawn_pane``) have each *value* digested while keys remain
    visible — env-var-name-like keys are operator-debug-useful and
    rarely sensitive, while their values usually are.
    Known nested operation lists are summarized recursively so batched
    tool calls keep target metadata while redacting inner payloads.

    Examples
    --------
    Non-sensitive scalars pass through unchanged:

    >>> _summarize_args({"pane_id": "%1", "bracket": True})
    {'pane_id': '%1', 'bracket': True}

    Sensitive payload names are replaced by a digest dict:

    >>> _summarize_args({"keys": "rm -rf /"})["keys"]["len"]
    8

    Sensitive dict-shaped payloads keep their keys but digest values:

    >>> redacted = _summarize_args({"environment": {"FOO": "bar"}})
    >>> redacted["environment"]["FOO"]["len"]
    3
    >>> "bar" in str(redacted)
    False
    """
    summary: dict[str, t.Any] = {}
    for key, value in args.items():
        if key in _SENSITIVE_ARG_NAMES and isinstance(value, str):
            summary[key] = _redact_digest(value)
        elif key in _SENSITIVE_ARG_NAMES and isinstance(value, dict):
            summary[key] = {k: _redact_digest(str(v)) for k, v in value.items()}
        elif key in _NESTED_ARG_LIST_NAMES:
            if isinstance(value, list):
                summary[key] = [
                    _summarize_nested_operation_args(item)
                    if isinstance(item, dict)
                    else _redacted_value_shape(item)
                    for item in value
                ]
            else:
                summary[key] = _redacted_value_shape(value)
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
#: Matches FastMCP's stock 1 MB default so normal schema-bearing tool
#: responses stay below this global backstop. Tool-level caps remain
#: responsible for terminal-specific truncation metadata.
DEFAULT_RESPONSE_LIMIT_BYTES = 1_000_000


#: Failures a retry cannot fix. Each one names a thing that is not there, or a
#: request that cannot succeed as written, so re-running it buys a second tmux
#: round-trip and a backoff window in order to fail identically. They all
#: descend from :exc:`libtmux.exc.LibTmuxException`, which is the retry
#: trigger, so without this set they would all be retried.
#:
#: Order the entries most-general-first when reading: ``ObjectDoesNotExist``
#: already covers :exc:`libtmux.exc.TmuxObjectDoesNotExist`.


def _resolve_exc_types(*names: str) -> tuple[type[Exception], ...]:
    """Resolve libtmux exception types by name, dropping any this build lacks.

    libtmux 0.62.0 re-parented :exc:`~libtmux.exc.ObjectDoesNotExist` and
    :exc:`~libtmux.exc.MultipleObjectsReturned` under
    :exc:`~libtmux.exc.LibTmuxException`. The chainable-commands experiment pins
    libtmux to an older branch that may not define every name, so resolving by
    name and dropping what is absent keeps this module importable there. On the
    ``libtmux>=0.62.0`` floor the package targets, every name resolves and the
    set is complete.
    """
    return tuple(
        exc_type
        for name in names
        if (exc_type := getattr(libtmux_exc, name, None)) is not None
    )


NON_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = _resolve_exc_types(
    "ObjectDoesNotExist",
    "MultipleObjectsReturned",
    "PaneNotFound",
    "NoWindowsExist",
    "BadSessionName",
    "TmuxSessionExists",
    "TmuxCommandNotFound",
)


class _SkipDeterministicFailures(RetryMiddleware):
    """A :class:`RetryMiddleware` that declines to retry what cannot succeed.

    fastmcp decides whether to retry in ``_should_retry``, matching the error
    and, one hop down, its ``__cause__`` -- which is where the real failure
    lives, because ``handle_tool_errors`` re-raises every libtmux error as an
    ``ExpectedToolError`` chained off the original. This narrows that decision
    with :data:`NON_RETRYABLE_EXCEPTIONS`, checking the same two places.
    """

    def _should_retry(self, error: Exception) -> bool:
        """Return ``False`` for a failure a second attempt cannot change."""
        cause = error.__cause__
        if isinstance(error, NON_RETRYABLE_EXCEPTIONS) or (
            cause is not None and isinstance(cause, NON_RETRYABLE_EXCEPTIONS)
        ):
            return False
        return super()._should_retry(error)


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

    Default retry trigger is :exc:`libtmux.exc.LibTmuxException` —
    libtmux wraps the subprocess failures we actually want to retry
    (socket EAGAIN, transient connect errors). The fastmcp default
    ``(ConnectionError, TimeoutError)`` does NOT match these, so the
    upstream defaults would be a silent no-op.

    That trigger is a whole family, though, and most of it is not
    transient: a pane that does not exist will not exist on the second
    look. :data:`NON_RETRYABLE_EXCEPTIONS` carves those out, so a stale
    id fails once instead of costing a backoff window and a second tmux
    round-trip to fail again.

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
        self._retry = _SkipDeterministicFailures(
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

    FastMCP's stock ``ResponseLimitingMiddleware`` truncates the
    tail of the response (keeps the start, appends a suffix). That's
    exactly wrong for terminal scrollback, where the active shell
    prompt and most recent command output live at the **bottom** of
    the buffer. This subclass overrides ``_truncate_to_result`` to
    drop the head instead, prefixing a single truncation-header line
    so callers can detect the cap fired.

    Used as a global backstop for :func:`libtmux_mcp.tools.pane_tools.capture_pane`,
    :func:`libtmux_mcp.tools.pane_tools.capture_since`,
    :func:`libtmux_mcp.tools.pane_tools.snapshot_pane`, and
    :func:`libtmux_mcp.tools.pane_tools.search_panes`. Per-tool
    caps at the tool layer fire first under normal operation; this
    middleware catches pathological output from future tools that
    forget to declare their own bounds.

    Error results keep their ``is_error`` flag through truncation.
    The stock truncation path rebuilds the result without it, which
    turns an oversized error (e.g. a validation message echoing a
    huge argument) into an apparent success — MCP clients then
    validate the truncated text against the tool's output schema and
    fail with a transport-level error instead of delivering the tool
    error. ``meta`` (``error_type`` / ``expected`` / ``suggestion``)
    already survives via the base class, and tail-preservation keeps
    the suggestion line, which sits at the end of the text.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: t.Any,
    ) -> t.Any:
        """Apply the size cap without dropping ``is_error``."""
        inner: t.Any = None

        async def _capture(
            context: MiddlewareContext[CallToolRequestParams],
        ) -> ToolResult:
            # ``context`` (not ``ctx``): fastmcp's CallNext protocol
            # matches the parameter name, not just the shape.
            nonlocal inner
            inner = await call_next(context)
            return t.cast("ToolResult", inner)

        result = await super().on_call_tool(context, _capture)
        if result is not inner and isinstance(inner, ToolResult) and inner.is_error:
            # The base class truncated and rebuilt the result; restore
            # the error flag it dropped.
            return ToolResult(
                content=result.content,
                meta=result.meta,
                is_error=True,
            )
        return result

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
