"""Tests for error translation at the tool boundary."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError
from libtmux import exc

from libtmux_mcp.tools.hook_tools import show_hooks
from libtmux_mcp.tools.option_tools import show_option

if t.TYPE_CHECKING:
    from libtmux.server import Server


# ---------------------------------------------------------------------------
# Error-handler decorator tests
# ---------------------------------------------------------------------------


def test_handle_tool_errors_passes_value_through() -> None:
    """A successful sync call returns the function's result untouched."""
    from libtmux_mcp._errors import handle_tool_errors

    @handle_tool_errors
    def _ok(x: int) -> int:
        return x * 2

    assert _ok(3) == 6


def test_handle_tool_errors_translates_libtmux_exception() -> None:
    """Libtmux errors are remapped to ``ToolError``."""
    from libtmux_mcp._errors import handle_tool_errors

    err_msg = "session foo already exists"

    @handle_tool_errors
    def _raiser() -> None:
        raise exc.TmuxSessionExists(err_msg)

    with pytest.raises(ToolError, match=err_msg):
        _raiser()


def test_handle_tool_errors_preserves_existing_tool_error() -> None:
    """An explicit ``ToolError`` is not rewrapped."""
    from libtmux_mcp._errors import handle_tool_errors

    sentinel = ToolError("explicit message")

    @handle_tool_errors
    def _raiser() -> None:
        raise sentinel

    with pytest.raises(ToolError) as excinfo:
        _raiser()
    assert excinfo.value is sentinel


def test_handle_tool_errors_async_passes_value_through() -> None:
    """Successful async tools return their result normally."""
    import asyncio

    from libtmux_mcp._errors import handle_tool_errors_async

    @handle_tool_errors_async
    async def _ok(x: int) -> int:
        return x + 5

    assert asyncio.run(_ok(10)) == 15


def test_handle_tool_errors_async_translates_libtmux_exception() -> None:
    """Async libtmux errors are remapped to ``ToolError`` consistently."""
    import asyncio

    from libtmux_mcp._errors import handle_tool_errors_async

    msg = "%99"

    @handle_tool_errors_async
    async def _raiser() -> None:
        raise exc.PaneNotFound(msg)

    with pytest.raises(ToolError, match="Pane not found"):
        asyncio.run(_raiser())


def test_handle_tool_errors_async_preserves_tool_error() -> None:
    """Async tools re-raise explicit ``ToolError`` without rewrapping."""
    import asyncio

    from libtmux_mcp._errors import handle_tool_errors_async

    sentinel = ToolError("explicit async message")

    @handle_tool_errors_async
    async def _raiser() -> None:
        raise sentinel

    with pytest.raises(ToolError) as excinfo:
        asyncio.run(_raiser())
    assert excinfo.value is sentinel


def test_handle_tool_errors_async_wraps_unexpected_exception() -> None:
    """Non-libtmux exceptions are wrapped with a typed prefix."""
    import asyncio

    from libtmux_mcp._errors import handle_tool_errors_async

    msg = "boom"

    @handle_tool_errors_async
    async def _raiser() -> None:
        raise RuntimeError(msg)

    with pytest.raises(ToolError, match=r"Unexpected error: RuntimeError: boom"):
        asyncio.run(_raiser())


# ---------------------------------------------------------------------------
# ExpectedToolError log-level tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [
        exc.TmuxSessionExists("session foo already exists"),
        exc.BadSessionName("bad name"),
        exc.TmuxObjectDoesNotExist("@99"),
        exc.ObjectDoesNotExist(query={"window_name": "gone"}),
        exc.MultipleObjectsReturned(count=2, query={"pane_id": "%0"}),
        exc.PaneNotFound("%99"),
        exc.LibTmuxException("server gone"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_map_exception_expected_failures_log_at_warning(
    raised: Exception,
) -> None:
    """Agent-correctable libtmux failures map to WARNING-level errors."""
    import logging

    from libtmux_mcp._errors import ExpectedToolError, _map_exception_to_tool_error

    mapped = _map_exception_to_tool_error("some_tool", raised)
    assert isinstance(mapped, ExpectedToolError)
    assert mapped.log_level == logging.WARNING


@pytest.mark.parametrize(
    "raised",
    [
        exc.TmuxCommandNotFound("tmux missing"),
        RuntimeError("boom"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_map_exception_operator_faults_stay_at_error(raised: Exception) -> None:
    """Environment faults and unexpected bugs keep the ERROR default."""
    import logging

    from libtmux_mcp._errors import ExpectedToolError, _map_exception_to_tool_error

    mapped = _map_exception_to_tool_error("some_tool", raised)
    assert not isinstance(mapped, ExpectedToolError)
    assert mapped.log_level == logging.ERROR


def test_map_exception_explains_a_newline_in_a_format_value() -> None:
    """The newline-in-a-path parse failure becomes actionable.

    libtmux <= 0.62.0 splits ``-F`` output one line per object, so a
    newline inside a value breaks its strict ``zip`` and every pane on
    that server stops resolving. It arrives as a bare ``ValueError`` and
    previously reached the agent as "Unexpected error", at ERROR,
    naming nothing it could act on.
    """
    from libtmux_mcp._errors import ExpectedToolError, _map_exception_to_tool_error

    raised = ValueError("zip() argument 2 is shorter than argument 1")
    mapped = _map_exception_to_tool_error("list_panes", raised)

    assert isinstance(mapped, ExpectedToolError)
    assert "newline" in str(mapped)
    assert mapped.suggestion is not None
    assert "pane_current_path" in mapped.suggestion


def test_map_exception_does_not_double_the_pane_prefix() -> None:
    """``Pane not found: Pane not found: %9`` said it twice.

    ``exc.PaneNotFound`` already prefixes its own message, and the
    mapper prefixed it again — visible on the most frequently hit error
    in the server.
    """
    from libtmux_mcp._errors import _map_exception_to_tool_error

    raised = exc.PaneNotFound("%9999")
    assert str(raised) == "Pane not found: %9999"

    mapped = _map_exception_to_tool_error("get_pane_info", raised)

    assert str(mapped) == "Pane not found: %9999"


def test_expected_tool_error_logs_warning_through_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fastmcp's server-layer error log honors ``ExpectedToolError.log_level``.

    Uses a minimal FastMCP instance (no middleware stack) so the
    assertion isolates fastmcp's own ``Error calling tool`` record —
    the project middleware's log behavior is covered in
    ``test_middleware.py``.
    """
    import asyncio
    import logging

    from fastmcp import Client, FastMCP

    from libtmux_mcp._errors import ExpectedToolError

    probe = FastMCP(name="probe")

    @probe.tool
    def fail_expected() -> str:
        msg = "Pane not found: %99"
        raise ExpectedToolError(msg)

    async def _call() -> None:
        async with Client(probe) as client:
            await client.call_tool("fail_expected", raise_on_error=False)

    with caplog.at_level(logging.DEBUG):
        asyncio.run(_call())

    records = [r for r in caplog.records if "Error calling tool" in r.message]
    assert records, "expected fastmcp to log the tool failure"
    assert all(r.levelno == logging.WARNING for r in records)


@pytest.mark.parametrize(
    ("raised", "expected_suggestion_fragment"),
    [
        (exc.TmuxObjectDoesNotExist("@99"), "list_sessions / list_windows"),
        (
            exc.MultipleObjectsReturned(count=2, query={"pane_id": "%0"}),
            "Target it by id",
        ),
        (exc.PaneNotFound("%99"), "list_panes"),
        (exc.TmuxSessionExists("dup"), None),
        (exc.BadSessionName("bad:name"), None),
        (exc.LibTmuxException("transient"), None),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v),
)
def test_map_exception_suggestion_policy(
    raised: Exception,
    expected_suggestion_fragment: str | None,
) -> None:
    """Only the not-found branches carry agent-facing recovery hints.

    Discovery tools are the canonical fix for stale/guessed ids — the
    most common agent mistake. The other expected branches stay
    hint-free until real transcripts show agents flailing on them.
    """
    from libtmux_mcp._errors import _map_exception_to_tool_error

    mapped = _map_exception_to_tool_error("some_tool", raised)
    suggestion = getattr(mapped, "suggestion", None)
    if expected_suggestion_fragment is None:
        assert suggestion is None
    else:
        assert suggestion is not None
        assert expected_suggestion_fragment in suggestion


def test_validation_refusals_echo_what_the_caller_sent(mcp_server: Server) -> None:
    """A refusal that states a rule without the value is half an answer.

    Most of this tree already echoes -- ``offset``, ``limit``,
    ``scroll_up`` and the batch ``timeout`` all say "received X". These
    five did not, which is the same one-tool-does-one-thing asymmetry
    the rest of this branch has been closing. A caller who typo'd
    ``"STOP"`` was told the rule and left to spot their own mistake.

    One test over the set rather than one per message: the property is
    shared, and five near-identical tests would be bloat.
    """
    import asyncio

    from libtmux_mcp.models import SendKeysOperation
    from libtmux_mcp.tools.pane_tools.io import run_command, send_keys_batch
    from libtmux_mcp.tools.pane_tools.layout import resize_pane

    socket = mcp_server.socket_name
    cases: list[tuple[t.Callable[[], object], str]] = [
        (
            lambda: send_keys_batch(
                operations=[SendKeysOperation(pane_id="%0", keys="x")],
                on_error=t.cast("t.Any", "STOP"),
                socket_name=socket,
            ),
            "'STOP'",
        ),
        (
            lambda: asyncio.run(
                run_command(
                    command="true", pane_id="%0", timeout=-1, socket_name=socket
                )
            ),
            "-1",
        ),
        (
            lambda: show_hooks(target="nope", socket_name=socket),
            "'nope'",
        ),
        (
            lambda: show_option(option="status", target="nope", socket_name=socket),
            "'nope'",
        ),
        (
            lambda: resize_pane(pane_id="%0", zoom=True, height=10, socket_name=socket),
            "zoom=True",
        ),
    ]
    for call, expected in cases:
        with pytest.raises(ToolError) as excinfo:
            call()
        assert expected in str(excinfo.value), (
            f"refusal did not echo {expected}: {excinfo.value}"
        )
