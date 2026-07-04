"""Tests for the opt-in engine-ops watcher tools."""

from __future__ import annotations

import asyncio
import typing as t

import pytest
from fastmcp import Client, FastMCP

from libtmux_mcp.tools import engine_watch

if t.TYPE_CHECKING:
    from collections.abc import Coroutine

    from libtmux.session import Session

_T = t.TypeVar("_T")


def _run(coro: Coroutine[t.Any, t.Any, _T]) -> _T:
    """Run *coro* synchronously (the repo's async-test convention)."""
    return asyncio.run(coro)


def _tool_names(mcp: FastMCP) -> set[str]:
    """List the visible tool names on *mcp* via an in-process client."""

    async def _go() -> set[str]:
        async with Client(mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    return _run(_go())


def _registered(mode: str | None, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """Return a fresh server with engine_watch registered under *mode* (off=None)."""
    if mode is None:
        monkeypatch.delenv(engine_watch.ENGINE_OPS_ENV, raising=False)
    else:
        monkeypatch.setenv(engine_watch.ENGINE_OPS_ENV, "1")
        monkeypatch.setenv("LIBTMUX_MCP_EVENTS", mode)
    mcp = FastMCP("test")
    engine_watch.register(mcp)
    return mcp


class _ModeCase(t.NamedTuple):
    """An event mode and the watcher tools it must register."""

    test_id: str
    mode: str
    tools: frozenset[str]


_MODE_CASES: tuple[_ModeCase, ...] = (
    _ModeCase("push", "push", frozenset({"wait_for_output", "watch_events"})),
    _ModeCase("pull", "pull", frozenset({"wait_for_output", "poll_events"})),
    _ModeCase(
        "both",
        "both",
        frozenset({"wait_for_output", "watch_events", "poll_events"}),
    ),
)


@pytest.mark.parametrize("case", _MODE_CASES, ids=[c.test_id for c in _MODE_CASES])
def test_watcher_tools_registered_per_mode(
    case: _ModeCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each event mode registers its watcher tools; wait_for_output is always on."""
    tools = _tool_names(_registered(case.mode, monkeypatch))
    assert case.tools <= tools
    _run(engine_watch.ashutdown())  # engine never started here -> harmless reset


def test_register_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag off, no watcher tools are registered."""
    assert "wait_for_output" not in _tool_names(_registered(None, monkeypatch))


def test_no_per_op_tools_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the watcher tools are added -- no op_* clutter."""
    assert not any(
        n.startswith("op_") for n in _tool_names(_registered("both", monkeypatch))
    )


def test_lifecycle_is_noop_when_off() -> None:
    """astartup/ashutdown are safe no-ops when the tier was never registered."""
    engine_watch._engine = None
    _run(engine_watch.astartup())
    _run(engine_watch.ashutdown())


def test_wait_for_output_settles_live(session: Session) -> None:
    """The registered monitor folds a real pane's output and settles when quiet."""
    from libtmux.experimental.engines import AsyncControlModeEngine
    from libtmux.experimental.engines.base import CommandRequest
    from libtmux.experimental.mcp.events import register_events

    server = session.server
    pane = session.active_window.active_pane
    assert pane is not None and pane.pane_id is not None
    pane_id = pane.pane_id

    async def _go() -> t.Any:
        async with AsyncControlModeEngine.for_server(server) as engine:
            mcp = FastMCP("test")
            register_events(mcp, engine, mode="push")
            async with Client(mcp) as client:

                async def _produce() -> None:
                    await asyncio.sleep(0.3)  # let the monitor subscribe first
                    await engine.run(
                        CommandRequest.from_args(
                            "send-keys",
                            "-t",
                            pane_id,
                            "echo WATCH_OK",
                            "Enter",
                        ),
                    )

                producer = asyncio.ensure_future(_produce())
                try:
                    result = await client.call_tool(
                        "wait_for_output",
                        {"target": pane_id, "settle_ms": 400, "timeout": 10.0},
                    )
                finally:
                    await producer
                return result.data

    data = _run(_go())
    assert data.pane_id == pane_id
    assert data.reason in ("settled", "byte_cap")
    assert "WATCH_OK" in data.captured_text
