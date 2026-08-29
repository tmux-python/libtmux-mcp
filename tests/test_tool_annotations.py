"""Tests that each tool's advertised MCP hints match what it does.

MCP defines ``destructiveHint: false`` as a positive claim of
additive-only updates and ``true`` as the cautious default, so a hint
is a statement to every connected client rather than a severity label.
These tests assert the statement per tool.
"""

from __future__ import annotations

import typing as t

import pytest

from libtmux_mcp.server import build_mcp_server

from .conftest import wire_annotations

#: Tools whose caller-supplied payload reaches a program that runs it —
#: a shell prompt, a pane's process, or the command ``pipe_pane`` feeds.
#: Membership is a fact about where the value lands, not about the
#: parameter's name, so it is listed rather than derived from a schema.
PANE_INPUT_TOOLS = frozenset(
    {
        "paste_buffer",
        "paste_text",
        "pipe_pane",
        "run_command",
        "send_keys",
        "send_keys_batch",
    }
)


@pytest.fixture(scope="module")
def advertised_tools() -> dict[str, t.Any]:
    """Return every registered tool keyed by name, as clients see it."""
    import asyncio

    mcp = build_mcp_server()
    tools = asyncio.run(mcp.list_tools())
    return {tool.name: tool for tool in tools}


def test_every_tool_advertises_all_four_hints(
    advertised_tools: dict[str, t.Any],
) -> None:
    """No tool leaves a client to fall back on a protocol default."""
    expected = {
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
    }
    for name, tool in advertised_tools.items():
        assert set(wire_annotations(tool)) >= expected, name


def test_every_pane_input_tool_is_registered(
    advertised_tools: dict[str, t.Any],
) -> None:
    """The list below names tools that exist, so a rename cannot mute it."""
    assert set(advertised_tools) >= PANE_INPUT_TOOLS


@pytest.mark.parametrize("name", sorted(PANE_INPUT_TOOLS))
def test_pane_input_tools_do_not_claim_additive_updates(
    advertised_tools: dict[str, t.Any],
    name: str,
) -> None:
    """Input a program executes is not an additive update, and never repeats."""
    hints = wire_annotations(advertised_tools[name])

    assert hints["destructiveHint"] is True
    assert hints["idempotentHint"] is False
    assert hints["openWorldHint"] is True
