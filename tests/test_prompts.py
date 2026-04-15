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
    assert "tmux wait-for -S libtmux_mcp_wait_" in text
    assert "wait_for_channel" in text
    # Exit-status preservation is the whole point — pin it.
    assert "exit $__mcp_status" in text


def test_run_and_wait_channel_is_uuid_scoped() -> None:
    """Each ``run_and_wait`` call embeds a unique wait-for channel.

    Regression guard for the critical bug where every call hardcoded
    ``mcp_done``, so concurrent agents racing on tmux's server-global
    channel namespace would cross-signal each other. Now the channel
    is ``libtmux_mcp_wait_<uuid4hex[:8]>``, fresh per invocation and
    consistent within one invocation (the name that appears in the
    ``send_keys`` payload must match the ``wait_for_channel`` call).
    """
    import re

    from libtmux_mcp.prompts.recipes import run_and_wait

    first = run_and_wait(command="pytest", pane_id="%1")
    second = run_and_wait(command="pytest", pane_id="%1")

    pattern = re.compile(r"libtmux_mcp_wait_[0-9a-f]{8}")
    first_matches = pattern.findall(first)
    second_matches = pattern.findall(second)

    # Two occurrences per rendering: one inside send_keys, one in
    # wait_for_channel. Both must be the SAME channel name within a
    # single rendering (consistency).
    assert len(first_matches) == 2
    assert first_matches[0] == first_matches[1]
    assert len(second_matches) == 2
    assert second_matches[0] == second_matches[1]

    # And the two renderings must differ from each other (uniqueness).
    assert first_matches[0] != second_matches[0]


def test_run_and_wait_handles_quoted_commands() -> None:
    """Single quotes in the command don't corrupt the rendered keys=...

    Regression guard for the fragile ``keys='{command}; ...'`` wrap —
    a command like ``python -c 'print(1)'`` closed the surrounding
    single-quote prematurely, producing a syntactically invalid
    ``send_keys`` call in the prompt output. The fix uses ``repr()``
    so Python picks a quote style that round-trips safely.
    """
    import ast

    from libtmux_mcp.prompts.recipes import run_and_wait

    text = run_and_wait(command="python -c 'print(1)'", pane_id="%1")
    # Extract the ``keys=`` argument as a Python literal and confirm
    # it parses back to a string containing the original command.
    keys_line = next(line for line in text.splitlines() if "keys=" in line)
    _, _, payload = keys_line.partition("keys=")
    payload = payload.rstrip(",").strip()
    parsed = ast.literal_eval(payload)
    assert isinstance(parsed, str)
    assert "python -c 'print(1)'" in parsed


def test_interrupt_gracefully_does_not_escalate() -> None:
    """``interrupt_gracefully`` refuses SIGQUIT auto-escalation."""
    from libtmux_mcp.prompts.recipes import interrupt_gracefully

    text = interrupt_gracefully(pane_id="%3")
    assert "do NOT escalate automatically" in text
