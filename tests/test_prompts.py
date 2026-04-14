"""Tests for libtmux-mcp prompt surface."""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from libtmux_mcp.prompts import ENV_PROMPTS_AS_TOOLS, register_prompts


@pytest.fixture
def mcp_with_prompts() -> FastMCP:
    """Build a fresh FastMCP with the four prompt recipes registered."""
    mcp = FastMCP(name="test-prompts")
    register_prompts(mcp)
    return mcp


def test_prompts_registered(mcp_with_prompts: FastMCP) -> None:
    """Four recipes appear in the prompt registry."""
    prompts = asyncio.run(mcp_with_prompts.list_prompts())
    names = {p.name for p in prompts}
    assert "run_and_wait" in names
    assert "diagnose_failing_pane" in names
    assert "build_dev_workspace" in names
    assert "interrupt_gracefully" in names


def test_prompts_as_tools_gated_off_by_default(mcp_with_prompts: FastMCP) -> None:
    """Without the env var, PromptsAsTools transform is not installed."""
    tools = asyncio.run(mcp_with_prompts.list_tools())
    names = {tool.name for tool in tools}
    assert "list_prompts" not in names
    assert "get_prompt" not in names


def test_prompts_as_tools_enabled_by_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting the env var installs PromptsAsTools."""
    monkeypatch.setenv(ENV_PROMPTS_AS_TOOLS, "1")
    mcp = FastMCP(name="test-prompts-as-tools")
    register_prompts(mcp)
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert "list_prompts" in names
    assert "get_prompt" in names


def test_run_and_wait_returns_string_template() -> None:
    """``run_and_wait`` prompt produces a string with the safe idiom."""
    from libtmux_mcp.prompts.recipes import run_and_wait

    text = run_and_wait(command="pytest", pane_id="%1", timeout=30.0)
    assert "tmux wait-for -S mcp_done" in text
    assert "wait_for_channel" in text
    # Exit-status preservation is the whole point — pin it.
    assert "exit $__mcp_status" in text


def test_interrupt_gracefully_does_not_escalate() -> None:
    """``interrupt_gracefully`` refuses SIGQUIT auto-escalation."""
    from libtmux_mcp.prompts.recipes import interrupt_gracefully

    text = interrupt_gracefully(pane_id="%3")
    assert "do NOT escalate automatically" in text
