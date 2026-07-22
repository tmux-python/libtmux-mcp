"""Tests for the opt-in engine-ops chained plan tools."""

from __future__ import annotations

import asyncio
import typing as t

import pytest
from fastmcp import Client, FastMCP
from libtmux.experimental.ops import LazyPlan, SendKeys, SplitWindow
from libtmux.experimental.ops._types import WindowId

from libtmux_mcp.tools import engine_plan

if t.TYPE_CHECKING:
    from collections.abc import Coroutine

_T = t.TypeVar("_T")


def _forward_ref_plan_ops() -> list[dict[str, t.Any]]:
    """Serialize a 2-op plan whose send-keys targets the split's new pane."""
    plan = LazyPlan()
    pane = plan.add(SplitWindow(target=WindowId("@1")))
    plan.add(SendKeys(target=pane, keys="vim"))
    return plan.to_list()


def _run(coro: Coroutine[t.Any, t.Any, _T]) -> _T:
    """Run *coro* synchronously (the repo's async-test convention)."""
    return asyncio.run(coro)


def _tool_names(mcp: FastMCP) -> set[str]:
    """List the visible tool names on *mcp* via an in-process client."""

    async def _go() -> set[str]:
        async with Client(mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    return _run(_go())


def _registered(*, enabled: bool, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """Return a fresh server with engine_plan registered (or not) per *enabled*."""
    if enabled:
        monkeypatch.setenv(engine_plan.ENGINE_OPS_ENV, "1")
    else:
        monkeypatch.delenv(engine_plan.ENGINE_OPS_ENV, raising=False)
    mcp = FastMCP("test")
    engine_plan.register(mcp)
    return mcp


def test_enabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """enabled() reflects the opt-in environment flag."""
    monkeypatch.delenv(engine_plan.ENGINE_OPS_ENV, raising=False)
    assert engine_plan.enabled() is False
    monkeypatch.setenv(engine_plan.ENGINE_OPS_ENV, "1")
    assert engine_plan.enabled() is True


def test_register_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the flag off, no engine-ops tools are registered."""
    mcp = _registered(enabled=False, monkeypatch=monkeypatch)
    assert "preview_plan" not in _tool_names(mcp)


class _ChainToolCase(t.NamedTuple):
    """A chain-tier tool that must appear when the flag is on."""

    test_id: str
    tool: str


_CHAIN_TOOL_CASES: tuple[_ChainToolCase, ...] = (
    _ChainToolCase("preview_plan", "preview_plan"),
    _ChainToolCase("explain_plan", "explain_plan"),
    _ChainToolCase("result_schema", "result_schema"),
    _ChainToolCase("execute_plan", "execute_plan"),
)


@pytest.mark.parametrize(
    "case",
    _CHAIN_TOOL_CASES,
    ids=[c.test_id for c in _CHAIN_TOOL_CASES],
)
def test_chain_tool_registered_when_enabled(
    case: _ChainToolCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each chain-tier tool is registered when opted in."""
    mcp = _registered(enabled=True, monkeypatch=monkeypatch)
    assert case.tool in _tool_names(mcp)


def test_no_per_op_tools_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the plan tier is added -- the hidden op_* tools are not registered."""
    mcp = _registered(enabled=True, monkeypatch=monkeypatch)
    assert not any(name.startswith("op_") for name in _tool_names(mcp))


def test_preview_and_explain_a_folded_forward_ref_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain previews a forward-ref plan and explains how it folds."""
    mcp = _registered(enabled=True, monkeypatch=monkeypatch)
    ops = _forward_ref_plan_ops()

    async def _go() -> tuple[t.Any, t.Any]:
        async with Client(mcp) as client:
            preview = await client.call_tool("preview_plan", {"operations": ops})
            explain = await client.call_tool(
                "explain_plan",
                {"operations": ops, "planner": "marked"},
            )
        return preview.data, explain.data

    preview, explain = _run(_go())
    # the send-keys targets the not-yet-created pane, so preview cannot resolve it
    assert preview["ok"] is False
    # under the marked planner the create + its decorate fold into one dispatch
    assert explain["steps"][0]["reason"] == "marked-fold"
