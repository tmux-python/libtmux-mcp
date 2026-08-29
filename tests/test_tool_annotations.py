"""Tests that each tool's advertised MCP hints match what it does.

MCP defines ``destructiveHint: false`` as a positive claim of
additive-only updates and ``true`` as the cautious default, so a hint
is a statement to every connected client rather than a severity label.
These tests assert the statement per tool.
"""

from __future__ import annotations

import typing as t

import pytest

from libtmux_mcp.tools import register_tools

from .conftest import wire_annotations

#: Tools that start a process. The pane's program runs with the user's
#: authority and reaches whatever that user reaches, so the effect does
#: not stop at tmux.
SPAWN_TOOLS = frozenset(
    {
        "create_session",
        "create_window",
        "respawn_pane",
        "split_window",
    }
)

#: Spawn tools that additionally accept a command string to run in place
#: of the pane's configured process.
AUTHORED_COMMAND_TOOLS = frozenset({"respawn_pane", "split_window"})

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
    """Return every registered tool keyed by name, as clients see it.

    Registers into a fresh server rather than the production one, whose
    tier filter is fixed at import: reading that one would hide the
    mutating and destructive tools whenever ``LIBTMUX_SAFETY`` is set.
    """
    import asyncio

    from fastmcp import FastMCP

    mcp = FastMCP(name="test-tool-annotations")
    register_tools(mcp)
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


@pytest.mark.parametrize("name", sorted(SPAWN_TOOLS))
def test_spawn_tools_are_advertised_open_world(
    advertised_tools: dict[str, t.Any],
    name: str,
) -> None:
    """A pane's program runs with the user's authority, not inside tmux."""
    assert wire_annotations(advertised_tools[name])["openWorldHint"] is True


@pytest.mark.parametrize("name", sorted(AUTHORED_COMMAND_TOOLS))
def test_authored_command_spawns_do_not_claim_additive_updates(
    advertised_tools: dict[str, t.Any],
    name: str,
) -> None:
    """A caller-authored command replaces what the pane would have run."""
    assert wire_annotations(advertised_tools[name])["destructiveHint"] is True


@pytest.mark.parametrize("name", sorted(SPAWN_TOOLS))
def test_spawn_tools_are_not_idempotent(
    advertised_tools: dict[str, t.Any],
    name: str,
) -> None:
    """Calling a spawn again starts another process; it does not settle."""
    assert wire_annotations(advertised_tools[name])["idempotentHint"] is False


#: The only tools whose updates are additive-only. Everything else that
#: writes replaces or removes prior state, which MCP spells
#: ``destructiveHint: true`` however small the change.
ADDITIVE_TOOLS = frozenset(
    {
        "create_session",
        "create_window",
        "load_buffer",
        "signal_channel",
        "wait_for_channel",
    }
)


def test_only_additive_tools_claim_additive_updates(
    advertised_tools: dict[str, t.Any],
) -> None:
    """A new tool cannot quietly claim additive-only updates."""
    for name, tool in advertised_tools.items():
        hints = wire_annotations(tool)
        if hints["readOnlyHint"]:
            continue
        assert hints["destructiveHint"] is (name not in ADDITIVE_TOOLS), name


#: Read tools that return terminal content. What a pane holds arrived
#: from somewhere else — an SSH session, a package manager, a remote
#: agent — so the text crossed a trust boundary before this server saw
#: it, which is what ``openWorldHint`` tells a client.
TERMINAL_CONTENT_TOOLS = frozenset(
    {
        "capture_pane",
        "capture_since",
        "search_panes",
        "show_buffer",
        "snapshot_pane",
        "wait_for_text",
    }
)


@pytest.mark.parametrize("name", sorted(TERMINAL_CONTENT_TOOLS))
def test_terminal_content_reads_are_advertised_open_world(
    advertised_tools: dict[str, t.Any],
    name: str,
) -> None:
    """Returned pane text is untrusted, however read-only the call was."""
    assert wire_annotations(advertised_tools[name])["openWorldHint"] is True


def test_the_read_batch_carries_its_members_open_world_hint(
    advertised_tools: dict[str, t.Any],
) -> None:
    """A batch advertises the worst case of what it can invoke."""
    batch = advertised_tools["call_readonly_tools_batch"]
    assert wire_annotations(batch)["openWorldHint"] is True
