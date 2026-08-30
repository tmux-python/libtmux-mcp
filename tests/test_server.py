"""Tests for libtmux MCP server configuration."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap
import typing as t

import pytest

from libtmux_mcp._utils import TOOLSET_INSPECT, TOOLSET_MANAGE, TOOLSET_TEARDOWN
from libtmux_mcp.server import (
    _BASE_INSTRUCTIONS,
    DEFAULT_TOOLSETS,
    _build_instructions,
)


class BuildInstructionsFixture(t.NamedTuple):
    """Test fixture for _build_instructions."""

    test_id: str
    toolsets: frozenset[str]
    tmux_pane_env: str | None
    tmux_env: str | None
    expect_agent_context: bool
    expect_pane_id_in_text: str | None
    expect_socket_name: str | None
    expect_toolsets_in_text: str


BUILD_INSTRUCTIONS_FIXTURES: list[BuildInstructionsFixture] = [
    BuildInstructionsFixture(
        test_id="inside_tmux_full_context",
        toolsets=DEFAULT_TOOLSETS,
        tmux_pane_env="%42",
        tmux_env="/tmp/tmux-1000/default,12345,0",
        expect_agent_context=True,
        expect_pane_id_in_text="%42",
        expect_socket_name="default",
        expect_toolsets_in_text="execute, inspect, manage",
    ),
    BuildInstructionsFixture(
        test_id="outside_tmux_no_context",
        toolsets=DEFAULT_TOOLSETS,
        tmux_pane_env=None,
        tmux_env=None,
        expect_agent_context=False,
        expect_pane_id_in_text=None,
        expect_socket_name=None,
        expect_toolsets_in_text="execute, inspect, manage",
    ),
    BuildInstructionsFixture(
        test_id="pane_only_no_tmux_env",
        toolsets=DEFAULT_TOOLSETS,
        tmux_pane_env="%99",
        tmux_env=None,
        expect_agent_context=True,
        expect_pane_id_in_text="%99",
        expect_socket_name=None,
        expect_toolsets_in_text="execute, inspect, manage",
    ),
    BuildInstructionsFixture(
        test_id="inspect_only",
        toolsets=frozenset({TOOLSET_INSPECT}),
        tmux_pane_env=None,
        tmux_env=None,
        expect_agent_context=False,
        expect_pane_id_in_text=None,
        expect_socket_name=None,
        expect_toolsets_in_text="inspect",
    ),
    # The ladder could not express this: deletion without the typing tools.
    BuildInstructionsFixture(
        test_id="inspect_and_teardown",
        toolsets=frozenset({TOOLSET_INSPECT, TOOLSET_TEARDOWN}),
        tmux_pane_env=None,
        tmux_env=None,
        expect_agent_context=False,
        expect_pane_id_in_text=None,
        expect_socket_name=None,
        expect_toolsets_in_text="inspect, teardown",
    ),
]


class ToolsetsFixture(t.NamedTuple):
    """One ``LIBTMUX_TOOLSETS`` value and the surface it selects."""

    test_id: str
    env_value: str | None
    expected: frozenset[str]


TOOLSETS_FIXTURES: list[ToolsetsFixture] = [
    ToolsetsFixture("unset_takes_the_default", None, DEFAULT_TOOLSETS),
    ToolsetsFixture("single", "inspect", frozenset({"inspect"})),
    ToolsetsFixture(
        "teardown_without_execute",
        "inspect,teardown",
        frozenset({"inspect", "teardown"}),
    ),
    ToolsetsFixture(
        "whitespace_tolerated", " inspect , manage ", frozenset({"inspect", "manage"})
    ),
    ToolsetsFixture("empty_is_legal", "", frozenset()),
]


@pytest.mark.parametrize(
    ToolsetsFixture._fields,
    TOOLSETS_FIXTURES,
    ids=[f.test_id for f in TOOLSETS_FIXTURES],
)
def test_resolve_toolsets(
    test_id: str,
    env_value: str | None,
    expected: frozenset[str],
) -> None:
    """``LIBTMUX_TOOLSETS`` selects an unordered surface."""
    from libtmux_mcp.server import _resolve_toolsets

    assert test_id
    assert _resolve_toolsets(env_value) == expected


def test_an_unknown_toolset_fails_startup() -> None:
    """A typo must not silently widen or narrow the surface."""
    from libtmux_mcp.server import _resolve_toolsets

    with pytest.raises(RuntimeError, match="unknown toolsets: bogus"):
        _resolve_toolsets("inspect,bogus")


@pytest.mark.parametrize(
    ("variable", "tool_name"),
    [
        ("LIBTMUX_TOOLS", "definitely_not_a_tool"),
        ("LIBTMUX_EXCLUDE_TOOLS", "definitely_not_a_tool"),
        ("LIBTMUX_TOOLS", "get_prompt"),
    ],
    ids=["include-typo", "exclude-typo", "disabled-prompt-adapter"],
)
def test_an_unknown_tool_name_fails_server_startup(
    variable: str,
    tool_name: str,
) -> None:
    """A typo in an individual include or exclude fails closed."""
    code = textwrap.dedent(
        """
        import asyncio

        from fastmcp import Client

        from libtmux_mcp.server import build_mcp_server


        async def main():
            async with Client(build_mcp_server()):
                pass


        asyncio.run(main())
        """
    )
    env = {**os.environ, variable: tool_name}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode != 0
    assert f"{variable} names unknown tools: {tool_name}" in proc.stderr


@pytest.mark.parametrize("variable", ["LIBTMUX_TOOLS", "LIBTMUX_EXCLUDE_TOOLS"])
def test_generated_prompt_tool_names_validate_when_enabled(variable: str) -> None:
    """Validation includes tools produced by the prompt adapter transform."""
    code = textwrap.dedent(
        """
        import asyncio

        from fastmcp import Client

        from libtmux_mcp.server import build_mcp_server


        async def main():
            async with Client(build_mcp_server()):
                pass


        asyncio.run(main())
        """
    )
    env = {
        **os.environ,
        "LIBTMUX_MCP_PROMPTS_AS_TOOLS": "1",
        variable: "get_prompt",
    }
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize(
    ("tool_name", "selection"),
    [
        ("send_keys", {"LIBTMUX_TOOLSETS": "inspect"}),
        ("list_sessions", {"LIBTMUX_EXCLUDE_TOOLS": "list_sessions"}),
    ],
    ids=["toolset-omission", "explicit-exclusion"],
)
def test_a_hidden_tool_is_unknown_on_the_production_wire(
    tool_name: str,
    selection: dict[str, str],
) -> None:
    """FastMCP visibility rejects hidden tools before tool dispatch."""
    code = textwrap.dedent(
        f"""
        import asyncio

        from fastmcp import Client

        from libtmux_mcp.server import build_mcp_server


        async def main():
            async with Client(build_mcp_server()) as client:
                result = await client.call_tool(
                    {tool_name!r},
                    {{}},
                    raise_on_error=False,
                )
                print(result.content[0].text)


        asyncio.run(main())
        """
    )
    env = {**os.environ, **selection}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0
    assert f"Unknown tool: '{tool_name}'" in proc.stdout


@pytest.mark.parametrize(
    ("excluded_tool", "nested_success"),
    [("", True), ("list_servers", False)],
    ids=["named-wrapper-authority", "explicit-exclusion"],
)
def test_named_read_batch_reaches_only_unexcluded_inspect_tools(
    excluded_tool: str,
    nested_success: bool,
    tmp_path: pathlib.Path,
) -> None:
    """A named batch carries inspect authority without exposing nested tools."""
    code = textwrap.dedent(
        """
        import asyncio
        import json

        from fastmcp import Client

        from libtmux_mcp.server import build_mcp_server


        async def main():
            async with Client(build_mcp_server()) as client:
                tools = await client.list_tools()
                batch = await client.call_tool(
                    "call_read_tools_batch",
                    {
                        "operations": [
                            {
                                "tool": "list_servers",
                                "arguments": {},
                            }
                        ]
                    },
                    raise_on_error=False,
                )
                direct = await client.call_tool(
                    "list_servers",
                    {},
                    raise_on_error=False,
                )
                row = batch.structured_content["results"][0]
                print(
                    json.dumps(
                        {
                            "tools": [tool.name for tool in tools],
                            "nested_success": row["success"],
                            "nested_error": row["error"],
                            "direct_error": direct.content[0].text,
                        }
                    )
                )


        asyncio.run(main())
        """
    )
    env = {
        **os.environ,
        "LIBTMUX_TOOLSETS": "",
        "LIBTMUX_TOOLS": "call_read_tools_batch",
        "LIBTMUX_EXCLUDE_TOOLS": excluded_tool,
        "TMUX_TMPDIR": str(tmp_path),
    }
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["tools"] == ["call_read_tools_batch"]
    assert payload["nested_success"] is nested_success
    assert "Unknown tool: 'list_servers'" in payload["direct_error"]
    if excluded_tool:
        assert "Unknown tool: 'list_servers'" in payload["nested_error"]
    else:
        assert payload["nested_error"] is None


def test_the_retired_safety_variable_fails_startup() -> None:
    """`LIBTMUX_SAFETY` is gone; ignoring it could widen a surface."""
    code = textwrap.dedent(
        """
        import libtmux_mcp.server
        """
    )
    env = {**os.environ, "LIBTMUX_SAFETY": "destructive"}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode != 0
    assert "LIBTMUX_SAFETY has been removed" in proc.stderr
    assert "LIBTMUX_TOOLSETS" in proc.stderr


def test_run_server_pins_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_server passes an explicit stdio transport to FastMCP."""
    from libtmux_mcp import server as server_mod

    class FakeServer:
        transport: str | None = None

        def run(self, *, transport: str | None = None) -> None:
            self.transport = transport

    fake = FakeServer()

    monkeypatch.setattr(server_mod, "build_mcp_server", lambda: fake)

    server_mod.run_server()

    assert fake.transport == "stdio"


def test_base_instructions_content() -> None:
    """_BASE_INSTRUCTIONS contains key guidance for the LLM."""
    assert "tmux hierarchy" in _BASE_INSTRUCTIONS
    assert "pane_id" in _BASE_INSTRUCTIONS
    assert "search_panes" in _BASE_INSTRUCTIONS
    assert "metadata vs content" in _BASE_INSTRUCTIONS


def test_base_instructions_surface_flagship_read_tools() -> None:
    """_BASE_INSTRUCTIONS mentions the richer read tools by name.

    ``display_message`` (tmux format queries) and ``snapshot_pane``
    (content + metadata in one call) are strictly more expressive than
    ``capture_pane`` for most contexts, but agents that never see them
    named in the instructions default to ``capture_pane`` + a follow-up
    ``get_pane_info``. Naming both explicitly changes that default.
    """
    assert "display_message" in _BASE_INSTRUCTIONS
    assert "snapshot_pane" in _BASE_INSTRUCTIONS
    assert "capture_since" in _BASE_INSTRUCTIONS


def test_base_instructions_prefer_typed_completion_over_polling() -> None:
    """_BASE_INSTRUCTIONS names typed completion and observation primitives."""
    assert "run_command" in _BASE_INSTRUCTIONS
    assert "wait_for_channel" in _BASE_INSTRUCTIONS
    assert "capture_since" in _BASE_INSTRUCTIONS
    assert "wait_for_text" in _BASE_INSTRUCTIONS
    # The catch-all form replaced the separate wait_for_content_change
    # tool; the instructions must still name it so agents know the
    # "wait for any new output" affordance exists.
    assert "patterns=null" in _BASE_INSTRUCTIONS
    assert "stop=" in _BASE_INSTRUCTIONS
    assert "send_keys_batch" in _BASE_INSTRUCTIONS
    assert _BASE_INSTRUCTIONS.index("run_command") < _BASE_INSTRUCTIONS.index(
        "wait_for_channel"
    )
    # The channel primitive should be named before the fallbacks so an
    # agent that scans top-to-bottom encounters the cheaper option first.
    assert _BASE_INSTRUCTIONS.index("wait_for_channel") < _BASE_INSTRUCTIONS.index(
        "wait_for_text"
    )


def test_base_instructions_document_hook_boundary() -> None:
    """_BASE_INSTRUCTIONS explains hooks are inspection-only by design.

    Without this sentence agents waste a turn asking for ``set_hook`` or
    trying to write hooks through a nonexistent tool. Naming the
    boundary heads off the exploratory call.
    """
    assert "NO DEDICATED HOOK-WRITE TOOLS" in _BASE_INSTRUCTIONS
    assert "show_hooks" in _BASE_INSTRUCTIONS
    assert "tmux config file" in _BASE_INSTRUCTIONS


def test_hooks_gap_keeps_process_death_rationale() -> None:
    """Hook-gap segment carries the rationale, not just the rule.

    Defensively pinned to ``_INSTR_HOOKS_GAP`` rather than
    ``_BASE_INSTRUCTIONS`` so a future refactor that moves "tmux config
    file" into a different segment is caught here, not only by the
    line-173 test on the joined string.
    """
    from libtmux_mcp.server import _INSTR_HOOKS_GAP

    assert "survive process death" in _INSTR_HOOKS_GAP
    assert "tmux config file" in _INSTR_HOOKS_GAP


def test_base_instructions_document_socket_name_contract() -> None:
    """_BASE_INSTRUCTIONS frames the socket_name promise precisely.

    list_servers does NOT accept socket_name (it's the discovery tool —
    see server_tools.py:263-264 where the signature is
    ``list_servers(extra_socket_paths=...)``), so the previous "All
    tools accept socket_name" wording was a lie. The instruction now
    qualifies "Targeted tmux tools" and explicitly names list_servers
    as the documented exception, matching what
    test_registered_tools_accept_socket_name asserts at the schema
    level.
    """
    assert "Targeted tmux tools accept" in _BASE_INSTRUCTIONS
    assert "list_servers" in _BASE_INSTRUCTIONS
    assert "extra_socket_paths" in _BASE_INSTRUCTIONS


def test_registered_tools_accept_socket_name() -> None:
    """All registered tools (except list_servers) accept ``socket_name``.

    ``_BASE_INSTRUCTIONS`` promises this with ``list_servers`` as the
    documented exception (it discovers sockets via
    ``extra_socket_paths`` instead, see ``server_tools.py:263-264``).
    If a future tool registration drops ``socket_name``, this test
    catches the regression instead of silently making the agent-facing
    instructions a lie.
    """
    import asyncio
    import inspect

    from fastmcp import FastMCP
    from fastmcp.tools.function_tool import FunctionTool

    from libtmux_mcp.tools import register_tools
    from libtmux_mcp.tools.server_tools import SOCKET_NAME_EXEMPT

    mcp = FastMCP(name="socket-name-contract")
    register_tools(mcp)

    tools = asyncio.run(mcp.list_tools())
    assert tools, "register_tools should have registered at least one tool"
    for tool in tools:
        if tool.name in SOCKET_NAME_EXEMPT:
            continue
        assert isinstance(tool, FunctionTool), (
            f"Tool {tool.name!r} is not a FunctionTool; the registry "
            f"introspection assumes FastMCP wraps each registered "
            f"function with FunctionTool"
        )
        sig = inspect.signature(tool.fn)
        assert "socket_name" in sig.parameters, (
            f"Tool {tool.name!r} omits socket_name; either add it, "
            f"add to server_tools.SOCKET_NAME_EXEMPT, or update "
            f"_BASE_INSTRUCTIONS"
        )


def test_base_instructions_document_buffer_lifecycle() -> None:
    """_BASE_INSTRUCTIONS explains the buffer lifecycle + no list_buffers.

    The load/paste/delete triple is non-obvious, and agents otherwise
    expect a ``list_buffers`` affordance. The instruction prevents both
    confusions and surfaces the clipboard-privacy reason so the
    omission reads as deliberate, not missing.
    """
    assert "BUFFERS" in _BASE_INSTRUCTIONS
    assert "load_buffer" in _BASE_INSTRUCTIONS
    assert "paste_buffer" in _BASE_INSTRUCTIONS
    assert "delete_buffer" in _BASE_INSTRUCTIONS
    assert "BufferRef" in _BASE_INSTRUCTIONS
    assert "list_buffers" in _BASE_INSTRUCTIONS
    assert "clipboard history" in _BASE_INSTRUCTIONS


def test_build_instructions_documents_is_caller_workflow_inside_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The is_caller workflow sentence appears only when inside tmux.

    The sentence references "your pane is identified above", which is
    only true when ``TMUX_PANE`` is set and the agent-context line has
    been emitted. Outside tmux, the sentence would be a lie — so it
    lives inside the ``if tmux_pane:`` branch of ``_build_instructions``
    and must NOT appear in ``_BASE_INSTRUCTIONS`` itself.
    """
    # Outside tmux: the workflow sentence must NOT appear.
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    outside = _build_instructions(toolsets=frozenset({TOOLSET_MANAGE}))
    assert "whoami tool" not in outside
    assert "is_caller=true" not in outside

    # Inside tmux: the workflow sentence appears.
    monkeypatch.setenv("TMUX_PANE", "%42")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
    inside = _build_instructions(toolsets=frozenset({TOOLSET_MANAGE}))
    assert "is_caller=true" in inside
    assert "whoami tool" in inside
    assert "list_panes" in inside


def test_build_instructions_always_names_the_toolsets() -> None:
    """Instructions name the enabled toolsets and how to change them.

    They also say what hiding one does not do, because a model reading a
    short list is otherwise free to infer that the rest is unreachable.
    """
    result = _build_instructions(toolsets=frozenset({TOOLSET_MANAGE}))

    assert "Toolsets: manage" in result
    assert "LIBTMUX_TOOLSETS" in result
    assert "not what a pane can run" in result


@pytest.mark.parametrize(
    ("suppress_history", "expected_default"),
    [(False, "false"), (True, "true")],
    ids=["history-disabled", "history-enabled"],
)
def test_build_instructions_documents_semantic_history_default_and_raw_boundary(
    suppress_history: bool,
    expected_default: str,
) -> None:
    """Instructions expose the active semantic default and raw exclusion."""
    instructions = _build_instructions(suppress_history=suppress_history)

    assert (
        f"suppress_history={expected_default}: run_command inherits; "
        "raw send/batch/paste and spawn do not."
    ) in instructions


def test_build_instructions_defaults_semantic_history_suppression_on() -> None:
    """Instructions default command-history suppression to enabled."""
    instructions = _build_instructions()

    assert (
        "suppress_history=true: run_command inherits; "
        "raw send/batch/paste and spawn do not."
    ) in instructions


@pytest.mark.parametrize(
    "suppress_history",
    [False, True],
    ids=["history-disabled", "history-enabled"],
)
@pytest.mark.parametrize(
    ("toolset", "tmux_pane", "tmux_env"),
    [
        (TOOLSET_INSPECT, "%42", "/tmp/tmux-1000/default,12345,0"),
        (TOOLSET_MANAGE, "%42", "/tmp/tmux-1000/default,12345,0"),
        (TOOLSET_TEARDOWN, "%42", "/tmp/tmux-1000/default,12345,0"),
        (TOOLSET_INSPECT, "", ""),
        (TOOLSET_MANAGE, "", ""),
        (TOOLSET_TEARDOWN, "", ""),
        # Variable-length stress: longer socket name + multi-digit pane id.
        # Guards against future text additions tipping a realistic case
        # over the 2KB budget. Exercises BOTH axes — a multi-digit pane id
        # (TMUX_PANE) and a longer socket name (LIBTMUX_SOCKET). Margin
        # ~2 bytes; if a future text addition trips this, either trim
        # further or fall back to a tighter compression form (drop spaces
        # around ``/`` in HOOKS, drop spaces after colons in the toolset
        # paragraph) for additional bytes of margin.
        (TOOLSET_INSPECT, "%99", "/tmp/tmux-1000/dev-prod,12345,0"),
    ],
)
def test_full_instructions_under_2kb_across_toolsets_and_tmux_pane(
    monkeypatch: pytest.MonkeyPatch,
    suppress_history: bool,
    toolset: str,
    tmux_pane: str,
    tmux_env: str,
) -> None:
    """The transmitted instructions= string fits Claude Code's 2KB budget.

    The static ``_BASE_INSTRUCTIONS`` length is not the contract —
    ``_build_instructions`` appends a toolset block, an optional
    `inspect`-only hint, and an optional ``$TMUX_PANE`` agent-context
    block. The full transmitted string must be ≤ 2048 bytes for every
    (toolset, tmux_pane) combination, otherwise Claude Code silently
    truncates the agent-context block — the only server-side fix for
    "current window" anaphora.

    Includes a variable-length stress case (longer socket name +
    multi-digit pane id) so realistic runtime injections of
    ``TMUX_PANE`` / ``TMUX`` cannot push the total over the budget
    without the test catching it.
    """
    if tmux_pane:
        monkeypatch.setenv("TMUX_PANE", tmux_pane)
        monkeypatch.setenv("TMUX", tmux_env)
    else:
        monkeypatch.delenv("TMUX_PANE", raising=False)
        monkeypatch.delenv("TMUX", raising=False)

    instructions = _build_instructions(
        toolsets=frozenset({toolset}),
        suppress_history=suppress_history,
    )
    size = len(instructions.encode())
    assert size <= 2048, (
        f"toolset={toolset} tmux_pane={tmux_pane!r}: "
        f"{size} bytes exceeds Claude Code's 2KB ceiling"
    )


@pytest.mark.parametrize(
    "socket_name",
    ["s" * 4096, "界" * 2048],
    ids=["long-ascii", "long-multibyte"],
)
def test_instruction_budget_drops_oversized_socket_before_required_text(
    monkeypatch: pytest.MonkeyPatch,
    socket_name: str,
) -> None:
    """Untrusted socket names cannot displace required UTF-8 instructions."""
    monkeypatch.setenv("TMUX_PANE", "%42")
    monkeypatch.setenv("TMUX", f"/tmp/tmux-1000/{socket_name},12345,0")

    instructions = _build_instructions(
        toolsets=frozenset({TOOLSET_INSPECT}),
        suppress_history=True,
    )

    assert len(instructions.encode("utf-8")) <= 2048
    assert _BASE_INSTRUCTIONS in instructions
    assert (
        "suppress_history=true: run_command inherits; "
        "raw send/batch/paste and spawn do not."
    ) in instructions
    assert "Agent context" in instructions
    assert "%42" in instructions
    assert socket_name not in instructions


def test_instruction_budget_can_drop_all_oversized_optional_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required instructions survive when even pane-only context is too large."""
    oversized_pane = "%" + ("界" * 2048)
    monkeypatch.setenv("TMUX_PANE", oversized_pane)
    monkeypatch.setenv("TMUX", f"/tmp/tmux-1000/{'s' * 4096},12345,0")

    instructions = _build_instructions(
        toolsets=frozenset({TOOLSET_INSPECT}),
        suppress_history=False,
    )

    assert len(instructions.encode("utf-8")) <= 2048
    assert _BASE_INSTRUCTIONS in instructions
    assert (
        "suppress_history=false: run_command inherits; "
        "raw send/batch/paste and spawn do not."
    ) in instructions
    assert "Agent context" not in instructions


def test_base_instructions_document_scope() -> None:
    """``_BASE_INSTRUCTIONS`` carries an activation rule with anti-triggers.

    The SCOPE segment names positive triggers (pane, current, %, @, $)
    and explicit anti-triggers (browser/editor/GUI/Jupyter) plus a
    safety-valve clause for the ambiguous case. Without this segment,
    bare 'pane'/'window'/'session' rely on the LLM to *infer* the tmux
    context from each tool's description; with it, the LLM has explicit
    boundaries it can quote when the user's phrasing is ambiguous.
    """
    for required in (
        "TRIGGERS:",
        "ANTI-TRIGGERS:",
        "pane",
        "'%'",
        "'@'",
        "'$'",
        "VS Code",
        "i3",
        "Jupyter",
        "clarifying question",
    ):
        assert required in _BASE_INSTRUCTIONS, f"missing: {required!r}"


def test_scope_segment_carries_anti_triggers() -> None:
    """SCOPE segment carries the activation rule, not just _BASE_INSTRUCTIONS.

    Defensively pinned to ``_INSTR_SCOPE`` rather than the joined
    string so a future refactor that moves the SCOPE content to a
    different segment is caught here, not only by the
    test_base_instructions_document_scope test on the joined string.
    """
    from libtmux_mcp.server import _INSTR_SCOPE

    assert "TRIGGERS:" in _INSTR_SCOPE
    assert "ANTI-TRIGGERS:" in _INSTR_SCOPE
    assert "VS Code" in _INSTR_SCOPE
    assert "Jupyter" in _INSTR_SCOPE
    assert "clarifying question" in _INSTR_SCOPE


@pytest.mark.parametrize("toolset", [TOOLSET_INSPECT, TOOLSET_MANAGE, TOOLSET_TEARDOWN])
def test_probe_hint_visible_only_on_an_inspect_only_surface(
    monkeypatch: pytest.MonkeyPatch, toolset: str
) -> None:
    """The investigation hint appears only when `inspect` is all there is.

    A wrong guess is cheap there (worst case: an
    extra ``list_panes`` call) and expensive on a surface holding teardown
    tools (where ``kill_*`` is one mis-routed query away). Reuse the existing
    toolset classification instead of shipping a separate discoverability knob.
    """
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    instructions = _build_instructions(toolsets=frozenset({toolset}))
    if toolset == TOOLSET_INSPECT:
        assert "Probe snapshot_pane" in instructions
    else:
        assert "Probe snapshot_pane" not in instructions


# ---------------------------------------------------------------------------
# Tool title audit — display-time disambiguation contract
# ---------------------------------------------------------------------------

#: Tools whose title must include the word ``tmux``. Hierarchy nouns
#: (window, session, server, option, environment, hook, buffer, channel)
#: collide with browser / editor / WM / OS-channel domains; the qualifier
#: is load-bearing for display surfaces (Claude Code's tool catalog UI,
#: ``claude mcp list`` outputs). Title is NOT in BM25's search corpus
#: (verified vs FastMCP's _extract_searchable_text), so this lever is
#: purely human-readable disambiguation. ``display_message`` is included
#: because its title was pre-qualified as "Evaluate tmux Format String"
#: by an earlier rename — pinning it here guards against silent
#: regression to "Evaluate Format String".
_TMUX_QUALIFIED_TOOLS = frozenset(
    [
        # 5 server-level
        "list_sessions",
        "list_servers",
        "create_session",
        "kill_server",
        "get_server_info",
        # 6 session-level
        "list_windows",
        "get_session_info",
        "create_window",
        "rename_session",
        "kill_session",
        "select_window",
        # 8 window-level
        "list_panes",
        "get_window_info",
        "split_window",
        "rename_window",
        "kill_window",
        "select_layout",
        "resize_window",
        "move_window",
        # 2 option
        "show_option",
        "set_option",
        # 2 env
        "show_environment",
        "set_environment",
        # 2 hook
        "show_hooks",
        "show_hook",
        # 4 buffer
        "load_buffer",
        "paste_buffer",
        "show_buffer",
        "delete_buffer",
        # 2 wait_for channel
        "wait_for_channel",
        "signal_channel",
        # 1 pre-qualified pane tool — see docstring above
        "display_message",
    ]
)


# ---------------------------------------------------------------------------
# Discovery anchors — BM25 lexicon and alwaysLoad meta hints
# ---------------------------------------------------------------------------

#: The high-traffic discovery anchors. ToolSearch BM25-ranks
#: against tool ``description`` (FastMCP's griffe parser hands the
#: leading paragraph in), so the anchors carry a buried-synonym
#: lexicon plus an inline anti-trigger to widen the indexed surface
#: and add explicit boundaries.
_DISCOVERY_ANCHORS = frozenset(
    [
        "list_panes",
        "list_windows",
        "list_sessions",
        "snapshot_pane",
        "search_panes",
        "capture_pane",
        "capture_since",
    ]
)


#: Discovery anchors that carry the ``anthropic/alwaysLoad`` per-tool
#: meta hint. Inspect only — best-effort hint to Claude Code that
#: keeps a tiny tmux vocabulary always-visible without preloading
#: every tool's schema.
_ALWAYS_LOAD_ANCHORS = frozenset(["list_panes", "list_windows", "snapshot_pane"])


#: Verbs-of-art whose titles stay generic — they are tmux-specific
#: terms already and over-prefixing reads as visual chrome.
#: ``display_message`` is exempt from this set (already qualified as
#: "Evaluate tmux Format String"; pinned in _TMUX_QUALIFIED_TOOLS).
_VERBS_OF_ART = frozenset(
    [
        "send_keys",
        "send_keys_batch",
        "capture_pane",
        "capture_since",
        "snapshot_pane",
        "paste_text",
        "get_pane_info",
        "find_pane_by_position",
        "clear_pane",
        "search_panes",
        "wait_for_text",
        "select_pane",
        "swap_pane",
        "enter_copy_mode",
        "exit_copy_mode",
        "resize_pane",
        "kill_pane",
        "respawn_pane",
        "set_pane_title",
        "pipe_pane",
    ]
)


def test_server_advertised_as_tmux() -> None:
    """``serverInfo.name`` aligns with the README registration slug.

    Cross-client display fields show ``serverInfo.name``; aligning to
    ``tmux`` removes a papercut where users registering via the README
    get ``mcp__tmux__*`` tool prefixes but the protocol-handshake name
    still says ``libtmux``.
    """
    from libtmux_mcp.server import mcp

    assert mcp.name == "tmux"


def test_build_mcp_server_registers_catalog_idempotently() -> None:
    """The FastMCP factory returns a populated server every time."""
    import asyncio

    from libtmux_mcp.server import build_mcp_server

    first = build_mcp_server()
    second = build_mcp_server()

    assert second is first

    tools = {tool.name for tool in asyncio.run(first.list_tools())}
    prompts = {prompt.name for prompt in asyncio.run(first.list_prompts())}
    templates = {
        template.name for template in asyncio.run(first.list_resource_templates())
    }

    assert "list_sessions" in tools
    assert "snapshot_pane" in tools
    assert "run_and_wait" in prompts
    assert "get_sessions" in templates


def test_fastmcp_json_loads_registered_server() -> None:
    """The repo FastMCP manifest points at the populated server factory."""
    import asyncio
    import pathlib

    from fastmcp.utilities.inspect import inspect_fastmcp
    from fastmcp.utilities.mcp_server_config import MCPServerConfig

    config_path = pathlib.Path("fastmcp.json")
    assert config_path.is_file()

    config = MCPServerConfig.from_file(config_path)
    server = asyncio.run(config.source.load_server())
    info = asyncio.run(inspect_fastmcp(server))

    assert config.source.path == "src/libtmux_mcp/server.py"
    assert config.source.entrypoint == "build_mcp_server"
    assert config.deployment.transport == "stdio"
    assert {tool.name for tool in info.tools} >= {"list_sessions", "snapshot_pane"}
    assert {prompt.name for prompt in info.prompts} >= {"run_and_wait"}
    assert {template.name for template in info.templates} >= {"get_sessions"}


def test_discovery_anchor_descriptions_carry_tmux_and_synonyms() -> None:
    """The six discovery anchors carry tmux + a buried synonym in BM25 corpus.

    FastMCP's ``parse_docstring`` extracts the leading text block
    before the first ``Parameters`` / ``Returns`` section as
    ``tool.description``. Both that paragraph and any subsequent prose
    ride into the BM25 corpus, so burying terminal / shell /
    scrollback / multiplexer / workspace synonyms in natural prose
    widens the indexed lexicon without leaving a discovery-engineering
    artifact in user-facing ``--help`` output.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import register_tools

    mcp = FastMCP(name="desc-audit")
    register_tools(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    synonyms = {"terminal", "shell", "scrollback", "multiplexer", "workspace"}
    for tool_name in _DISCOVERY_ANCHORS:
        tool = tools.get(tool_name)
        assert tool is not None, f"tool not registered: {tool_name}"
        desc = (tool.description or "").lower()
        assert "tmux" in desc, f"{tool_name} description missing 'tmux'"
        assert any(s in desc for s in synonyms), (
            f"{tool_name} description missing a synonym from {synonyms}: {desc[:200]!r}"
        )


def test_discovery_anchors_marked_alwaysload() -> None:
    """``list_panes``, ``list_windows``, ``snapshot_pane`` carry alwaysLoad.

    Best-effort hint — FastMCP passes ``meta`` opaquely, so honoring
    is delegated to Claude Code where the field is documented at
    ``code.claude.com/docs/en/mcp`` (v2.1.121+). The test asserts only
    the positive contract; over-specifying the negative space is
    chrome.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import register_tools

    mcp = FastMCP(name="meta-audit")
    register_tools(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    for tool_name in _ALWAYS_LOAD_ANCHORS:
        tool = tools.get(tool_name)
        assert tool is not None, f"tool not registered: {tool_name}"
        meta = getattr(tool, "meta", None) or {}
        assert meta.get("anthropic/alwaysLoad") is True, (
            f"{tool_name} meta missing anthropic/alwaysLoad: {meta!r}"
        )


def test_hierarchy_tool_titles_carry_tmux_qualifier() -> None:
    """Hierarchy-noun titles include 'tmux' for display disambiguation.

    Without the qualifier, "List Windows" competes with browser /
    editor / WM MCPs that share the noun. Title is NOT BM25-indexed
    (FastMCP's _extract_searchable_text only concatenates name +
    description + parameter names + parameter descriptions), so this
    test guards the human-readable disambiguation contract for tool
    catalog UIs and ``claude mcp list``-style outputs only.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import register_tools

    mcp = FastMCP(name="title-audit")
    register_tools(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    for tool_name in _TMUX_QUALIFIED_TOOLS:
        tool = tools.get(tool_name)
        assert tool is not None, f"tool not registered: {tool_name}"
        assert tool.title is not None, f"{tool_name} missing title"
        assert "tmux" in tool.title.lower(), (
            f"{tool_name} title {tool.title!r} should include 'tmux'"
        )


def test_verbs_of_art_titles_unchanged() -> None:
    """Verb-of-art titles stay generic — over-prefixing is visual chrome.

    Send Keys, Pipe Pane, Snapshot Pane, Capture Pane, Paste Text,
    etc. are tmux-specific terms already. Adding ``tmux`` to the title
    delivers no display-disambiguation lift and inflates every tool
    catalog entry.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import register_tools

    mcp = FastMCP(name="verbs-of-art-audit")
    register_tools(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    for tool_name in _VERBS_OF_ART:
        tool = tools.get(tool_name)
        assert tool is not None, f"tool not registered: {tool_name}"
        assert tool.title is not None, f"{tool_name} missing title"
        assert "tmux" not in tool.title.lower(), (
            f"{tool_name} title {tool.title!r} should NOT include 'tmux' "
            "— it's a verb-of-art, already disambiguated by the verb"
        )


# ---------------------------------------------------------------------------
# Lifespan tests
# ---------------------------------------------------------------------------


def test_lifespan_missing_tmux_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup raises a clear RuntimeError when tmux is not on PATH."""
    import asyncio

    from libtmux_mcp.server import _lifespan, mcp

    def _missing_tmux(_name: str) -> None:
        return None

    monkeypatch.setattr("libtmux_mcp.server.shutil.which", _missing_tmux)

    async def _enter() -> None:
        async with _lifespan(mcp):
            pytest.fail("lifespan should have raised before yielding")

    with pytest.raises(RuntimeError, match="tmux binary not found"):
        asyncio.run(_enter())


def test_lifespan_clears_server_cache_on_exit() -> None:
    """Clean lifespan exit drops cached clients without touching tmux."""
    import asyncio

    from libtmux_mcp._utils import _server_cache
    from libtmux_mcp.server import _lifespan, mcp

    class RecordingServer:
        def __init__(self) -> None:
            self.commands: list[tuple[object, ...]] = []

        def cmd(self, *args: object) -> object:
            self.commands.append(args)
            return t.cast("t.Any", object())

    server = RecordingServer()
    _server_cache[("sentinel_socket", None, None)] = t.cast("t.Any", server)

    async def _cycle() -> None:
        async with _lifespan(mcp):
            # While the lifespan is active the cache still holds state.
            assert _server_cache

    asyncio.run(_cycle())
    assert _server_cache == {}
    assert server.commands == []


def test_server_constructed_with_lifespan() -> None:
    """The production FastMCP instance is wired with ``_lifespan``."""
    from libtmux_mcp.server import _lifespan, mcp

    assert mcp._lifespan is _lifespan
