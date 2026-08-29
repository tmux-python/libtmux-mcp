"""The error type every tool raises, and the boundary that shapes it.

`ExpectedToolError` marks a failure the caller can act on; anything else
is a bug and reaches the client as one. The two decorators put that
distinction at the tool boundary so no tool body repeats it.
"""

from __future__ import annotations

import functools
import logging
import typing as t

from fastmcp.exceptions import ToolError
from libtmux import exc

logger = logging.getLogger(__name__)


class ExpectedToolError(ToolError):
    """``ToolError`` for expected, agent-correctable failures.

    Defaults the error's ``log_level`` to ``WARNING`` (honored by
    fastmcp >= 3.3 when logging tool/resource failures) so routine
    validation errors, missing objects, and tier denials do not surface
    as ERROR records. Unexpected failures keep stock :class:`ToolError`
    and its ERROR default — those are the ones operators must see.

    Parameters
    ----------
    *args : object
        Positional arguments forwarded to :class:`ToolError`
        (typically the error message).
    log_level : int
        Level fastmcp's server layer logs this failure at. Defaults
        to ``logging.WARNING``.
    suggestion : str, optional
        Agent-facing recovery hint.
        :class:`~libtmux_mcp.middleware.ToolErrorResultMiddleware`
        appends it to the error result's text and mirrors it into the
        result's ``meta``.

    Examples
    --------
    >>> import logging
    >>> ExpectedToolError("Pane not found: %5").log_level == logging.WARNING
    True

    An explicit level still wins:

    >>> err = ExpectedToolError("noisy", log_level=logging.INFO)
    >>> err.log_level == logging.INFO
    True

    Catch sites that handle ``ToolError`` keep working — this is a
    plain subclass:

    >>> isinstance(ExpectedToolError("x"), ToolError)
    True

    An optional ``suggestion`` carries an agent-facing recovery hint;
    :class:`libtmux_mcp.middleware.ToolErrorResultMiddleware` surfaces
    it in the error result's text and ``meta``:

    >>> err = ExpectedToolError("Pane not found: %5",
    ...     suggestion="Call list_panes to discover valid pane ids.")
    >>> err.suggestion
    'Call list_panes to discover valid pane ids.'
    >>> ExpectedToolError("no hint").suggestion is None
    True
    """

    def __init__(
        self,
        *args: object,
        log_level: int = logging.WARNING,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(*args, log_level=log_level)
        self.suggestion = suggestion


P = t.ParamSpec("P")
R = t.TypeVar("R")


def _undouble(prefix: str, text: str) -> str:
    """Drop *prefix* from *text* when the wrapper is about to add it back."""
    return text.removeprefix(prefix)


def _is_format_newline_parse_error(e: BaseException) -> bool:
    """Detect libtmux failing to parse a format value containing a newline.

    libtmux <= 0.62.0 splits ``-F`` output one line per object, so a
    newline inside any value (a pane's current directory, most reachably)
    splits that record and its strict ``zip`` raises. It surfaces as a
    bare ``ValueError`` and would otherwise reach the agent as
    "Unexpected error", logged at ERROR, naming nothing it can act on.

    Matched on the message because the raise site is a stdlib ``zip``
    with no dedicated exception type. Kept even once the floor moves
    past the libtmux fix: the installed version is not ours to choose.
    """
    return isinstance(e, ValueError) and "zip()" in str(e)


def _map_exception_to_tool_error(fn_name: str, e: BaseException) -> ToolError:
    """Translate a libtmux / unexpected exception into a ``ToolError``.

    Shared between the sync and async ``handle_tool_errors*`` decorators
    so the two paths stay byte-for-byte identical in what agents see.

    Expected, agent-correctable failures map to
    :class:`ExpectedToolError` (logged at WARNING). Two cases stay at
    ERROR: a missing tmux binary (operator-environment fault that must
    be loud) and the unexpected catch-all (potential bug in this
    server).
    """
    if isinstance(e, exc.TmuxCommandNotFound):
        msg = "tmux binary not found. Ensure tmux is installed and in PATH."
        return ToolError(msg)
    if isinstance(e, exc.TmuxSessionExists):
        return ExpectedToolError(str(e))
    if isinstance(e, exc.BadSessionName):
        return ExpectedToolError(str(e))
    if isinstance(e, exc.ObjectDoesNotExist):
        return ExpectedToolError(
            f"Object not found: {e}",
            suggestion=(
                "Call list_sessions / list_windows / list_panes to discover valid ids."
            ),
        )
    if isinstance(e, exc.MultipleObjectsReturned):
        return ExpectedToolError(
            f"Ambiguous target: {e}",
            suggestion=(
                "A window shared between sessions is listed once per session that "
                "holds it, so a name or index can match more than one row. Target "
                "it by id (session_id / window_id / pane_id) instead."
            ),
        )
    if isinstance(e, exc.PaneNotFound):
        return ExpectedToolError(
            f"Pane not found: {_undouble('Pane not found: ', str(e))}",
            suggestion="Call list_panes to discover valid pane ids.",
        )
    if _is_format_newline_parse_error(e):
        return ExpectedToolError(
            "tmux listing could not be parsed: a format value contains a "
            "newline, almost always a pane whose current directory has one "
            "in its name. Every pane on this server is affected, not just "
            "that one, because pane lookup enumerates them all.",
            suggestion=(
                "Find it with: tmux list-panes -a -F "
                "'#{pane_id} #{pane_current_path}' | cat -A — then move or "
                "rename that directory. Upgrading libtmux also fixes it."
            ),
        )
    if isinstance(e, exc.LibTmuxException):
        return ExpectedToolError(f"tmux error: {e}")
    logger.exception("unexpected error in MCP tool %s", fn_name)
    return ToolError(f"Unexpected error: {type(e).__name__}: {e}")


def handle_tool_errors(
    fn: t.Callable[P, R],
) -> t.Callable[P, R]:
    """Decorate synchronous MCP tool functions with standardized error handling.

    Catches libtmux exceptions and re-raises them through
    :func:`_map_exception_to_tool_error` so MCP responses have
    ``isError=True`` with a descriptive message — expected,
    agent-correctable failures as :class:`ExpectedToolError` (logged
    at WARNING), the unexpected catch-all as stock ``ToolError``
    (logged at ERROR).

    The re-raise chains the original exception via ``from e``. Keep it
    single-level: :class:`~libtmux_mcp.middleware.ReadonlyRetryMiddleware`
    matches :exc:`libtmux.exc.LibTmuxException` by inspecting exactly
    one ``__cause__`` hop, so wrapping the mapped error again would
    silently disable readonly retries.

    Use :func:`handle_tool_errors_async` for ``async def`` tools — this
    wrapper only supports plain sync callables.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise _map_exception_to_tool_error(fn.__name__, e) from e

    return wrapper


def handle_tool_errors_async(
    fn: t.Callable[P, t.Coroutine[t.Any, t.Any, R]],
) -> t.Callable[P, t.Coroutine[t.Any, t.Any, R]]:
    """Decorate asynchronous MCP tool functions with standardized error handling.

    Async counterpart to :func:`handle_tool_errors`. Required for tools
    that accept a :class:`fastmcp.Context` parameter because Context's
    ``report_progress``/``elicit``/``read_resource`` methods are
    coroutines that only run inside ``async def`` tools.

    Maps the same libtmux exception set to the same messages and
    error classes as the sync decorator (expected failures as
    :class:`ExpectedToolError` at WARNING, the unexpected catch-all as
    stock ``ToolError`` at ERROR) by delegating to a shared helper,
    and chains the original exception via the same single-level
    ``from e`` that readonly retries depend on.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise _map_exception_to_tool_error(fn.__name__, e) from e

    return wrapper
