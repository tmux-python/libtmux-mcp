"""Tests for libtmux MCP server configuration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import typing as t

import pytest

from libtmux_mcp._utils import TAG_DESTRUCTIVE, TAG_MUTATING, TAG_READONLY
from libtmux_mcp.server import _BASE_INSTRUCTIONS, _build_instructions

if t.TYPE_CHECKING:
    from libtmux.server import Server

    from libtmux_mcp.server import _ServerCacheKey


class BuildInstructionsFixture(t.NamedTuple):
    """Test fixture for _build_instructions."""

    test_id: str
    safety_level: str
    tmux_pane_env: str | None
    tmux_env: str | None
    expect_agent_context: bool
    expect_pane_id_in_text: str | None
    expect_socket_name: str | None
    expect_safety_in_text: str | None


BUILD_INSTRUCTIONS_FIXTURES: list[BuildInstructionsFixture] = [
    BuildInstructionsFixture(
        test_id="inside_tmux_full_context",
        safety_level=TAG_MUTATING,
        tmux_pane_env="%42",
        tmux_env="/tmp/tmux-1000/default,12345,0",
        expect_agent_context=True,
        expect_pane_id_in_text="%42",
        expect_socket_name="default",
        expect_safety_in_text="mutating",
    ),
    BuildInstructionsFixture(
        test_id="outside_tmux_no_context",
        safety_level=TAG_MUTATING,
        tmux_pane_env=None,
        tmux_env=None,
        expect_agent_context=False,
        expect_pane_id_in_text=None,
        expect_socket_name=None,
        expect_safety_in_text="mutating",
    ),
    BuildInstructionsFixture(
        test_id="pane_only_no_tmux_env",
        safety_level=TAG_MUTATING,
        tmux_pane_env="%99",
        tmux_env=None,
        expect_agent_context=True,
        expect_pane_id_in_text="%99",
        expect_socket_name=None,
        expect_safety_in_text="mutating",
    ),
    BuildInstructionsFixture(
        test_id="readonly_safety_level",
        safety_level=TAG_READONLY,
        tmux_pane_env=None,
        tmux_env=None,
        expect_agent_context=False,
        expect_pane_id_in_text=None,
        expect_socket_name=None,
        expect_safety_in_text="readonly",
    ),
    BuildInstructionsFixture(
        test_id="destructive_safety_level",
        safety_level=TAG_DESTRUCTIVE,
        tmux_pane_env=None,
        tmux_env=None,
        expect_agent_context=False,
        expect_pane_id_in_text=None,
        expect_socket_name=None,
        expect_safety_in_text="destructive",
    ),
]


class SafetyLevelFixture(t.NamedTuple):
    """Test fixture for server safety-level resolution."""

    test_id: str
    env_value: str | None
    expected_level: str


SAFETY_LEVEL_FIXTURES: list[SafetyLevelFixture] = [
    SafetyLevelFixture("unset_defaults_mutating", None, TAG_MUTATING),
    SafetyLevelFixture("valid_readonly", TAG_READONLY, TAG_READONLY),
    SafetyLevelFixture("valid_mutating", TAG_MUTATING, TAG_MUTATING),
    SafetyLevelFixture("valid_destructive", TAG_DESTRUCTIVE, TAG_DESTRUCTIVE),
    SafetyLevelFixture("invalid_fails_closed", "read", TAG_READONLY),
]


@pytest.mark.parametrize(
    BuildInstructionsFixture._fields,
    BUILD_INSTRUCTIONS_FIXTURES,
    ids=[f.test_id for f in BUILD_INSTRUCTIONS_FIXTURES],
)
def test_build_instructions(
    monkeypatch: pytest.MonkeyPatch,
    test_id: str,
    safety_level: str,
    tmux_pane_env: str | None,
    tmux_env: str | None,
    expect_agent_context: bool,
    expect_pane_id_in_text: str | None,
    expect_socket_name: str | None,
    expect_safety_in_text: str | None,
) -> None:
    """_build_instructions includes agent context and safety level."""
    if tmux_pane_env is not None:
        monkeypatch.setenv("TMUX_PANE", tmux_pane_env)
    else:
        monkeypatch.delenv("TMUX_PANE", raising=False)

    if tmux_env is not None:
        monkeypatch.setenv("TMUX", tmux_env)
    else:
        monkeypatch.delenv("TMUX", raising=False)

    result = _build_instructions(safety_level=safety_level)

    # Base instructions are always present
    assert _BASE_INSTRUCTIONS in result

    if expect_agent_context:
        assert "Agent context" in result
    else:
        assert "Agent context" not in result

    if expect_pane_id_in_text is not None:
        assert expect_pane_id_in_text in result

    if expect_socket_name is not None:
        assert expect_socket_name in result

    if expect_safety_in_text is not None:
        assert f"Safety level: {expect_safety_in_text}" in result


@pytest.mark.parametrize(
    SafetyLevelFixture._fields,
    SAFETY_LEVEL_FIXTURES,
    ids=[f.test_id for f in SAFETY_LEVEL_FIXTURES],
)
def test_resolve_safety_level(
    test_id: str,
    env_value: str | None,
    expected_level: str,
) -> None:
    """Safety env values resolve to the server's effective tier."""
    from libtmux_mcp.server import _resolve_safety_level

    assert test_id
    assert _resolve_safety_level(env_value) == expected_level


def test_invalid_safety_env_hides_mutating_tools() -> None:
    """Invalid ``LIBTMUX_SAFETY`` values expose readonly tools only."""
    code = textwrap.dedent(
        """
        import asyncio
        import json

        from libtmux_mcp.server import build_mcp_server

        async def main():
            tools = await build_mcp_server().list_tools()
            names = {tool.name for tool in tools}
            print(json.dumps({
                "list_sessions": "list_sessions" in names,
                "send_keys": "send_keys" in names,
                "kill_pane": "kill_pane" in names,
            }))

        asyncio.run(main())
        """
    )
    env = {**os.environ, "LIBTMUX_SAFETY": "read"}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    result = json.loads(proc.stdout)

    assert result == {
        "list_sessions": True,
        "send_keys": False,
        "kill_pane": False,
    }


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


def test_base_instructions_prefer_wait_over_poll() -> None:
    """_BASE_INSTRUCTIONS names the wait family with the right primacy.

    ``wait_for_channel`` is the deterministic primitive (composes
    ``tmux wait-for -S``) and should appear first; ``wait_for_text``
    and ``wait_for_content_change`` are the fallbacks for output the
    agent doesn't author. Making the channel primitive discoverable
    from the instructions steers agents off the polling-scraper path
    for command-completion synchronization.
    """
    assert "wait_for_channel" in _BASE_INSTRUCTIONS
    assert "capture_since" in _BASE_INSTRUCTIONS
    assert "wait_for_text" in _BASE_INSTRUCTIONS
    assert "wait_for_content_change" in _BASE_INSTRUCTIONS
    # The channel primitive should be named before the fallbacks so an
    # agent that scans top-to-bottom encounters the cheaper option first.
    assert _BASE_INSTRUCTIONS.index("wait_for_channel") < _BASE_INSTRUCTIONS.index(
        "wait_for_text"
    )


def test_base_instructions_document_hook_boundary() -> None:
    """_BASE_INSTRUCTIONS explains hooks are read-only by design.

    Without this sentence agents waste a turn asking for ``set_hook`` or
    trying to write hooks through a nonexistent tool. Naming the
    boundary heads off the exploratory call.
    """
    assert "HOOKS ARE READ-ONLY" in _BASE_INSTRUCTIONS
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
    outside = _build_instructions(safety_level=TAG_MUTATING)
    assert "whoami tool" not in outside
    assert "is_caller=true" not in outside

    # Inside tmux: the workflow sentence appears.
    monkeypatch.setenv("TMUX_PANE", "%42")
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,12345,0")
    inside = _build_instructions(safety_level=TAG_MUTATING)
    assert "is_caller=true" in inside
    assert "whoami tool" in inside
    assert "list_panes" in inside


def test_build_instructions_always_includes_safety() -> None:
    """_build_instructions always includes the safety level."""
    result = _build_instructions(safety_level=TAG_MUTATING)
    assert "Safety level:" in result
    assert "LIBTMUX_SAFETY" in result


@pytest.mark.parametrize(
    ("tier", "tmux_pane", "tmux_env"),
    [
        (TAG_READONLY, "%42", "/tmp/tmux-1000/default,12345,0"),
        (TAG_MUTATING, "%42", "/tmp/tmux-1000/default,12345,0"),
        (TAG_DESTRUCTIVE, "%42", "/tmp/tmux-1000/default,12345,0"),
        (TAG_READONLY, "", ""),
        (TAG_MUTATING, "", ""),
        (TAG_DESTRUCTIVE, "", ""),
        # Variable-length stress: longer socket name + multi-digit pane id.
        # Guards against future text additions tipping a realistic case
        # over the 2KB budget. Exercises BOTH axes — a multi-digit pane id
        # (TMUX_PANE) and a longer socket name (LIBTMUX_SOCKET). Margin
        # ~2 bytes; if a future text addition trips this, either trim
        # further or fall back to a tighter compression form (drop spaces
        # around ``/`` in HOOKS, drop spaces after colons in the safety
        # paragraph) for additional bytes of margin.
        (TAG_READONLY, "%99", "/tmp/tmux-1000/dev-prod,12345,0"),
    ],
)
def test_full_instructions_under_2kb_across_tiers_and_tmux_pane(
    monkeypatch: pytest.MonkeyPatch,
    tier: str,
    tmux_pane: str,
    tmux_env: str,
) -> None:
    """The transmitted instructions= string fits Claude Code's 2KB budget.

    The static ``_BASE_INSTRUCTIONS`` length is not the contract —
    ``_build_instructions`` appends a safety-tier block, an optional
    readonly-tier hint, and an optional ``$TMUX_PANE`` agent-context
    block. The full transmitted string must be ≤ 2048 bytes for every
    (tier, tmux_pane) combination, otherwise Claude Code silently
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

    instructions = _build_instructions(safety_level=tier)
    size = len(instructions.encode())
    assert size <= 2048, (
        f"tier={tier} tmux_pane={tmux_pane!r}: "
        f"{size} bytes exceeds Claude Code's 2KB ceiling"
    )


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


@pytest.mark.parametrize("tier", [TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE])
def test_readonly_hint_visible_only_on_readonly_tier(
    monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    """The 'Readonly mode:' investigation hint appears only on readonly.

    False-positive activation is cheap on readonly (worst case: an
    extra ``list_panes`` call) and expensive on mutating/destructive
    (where ``kill_*`` is one mis-routed query away). Reuse the existing
    safety axis instead of shipping a separate discoverability knob.
    """
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    instructions = _build_instructions(safety_level=tier)
    if tier == TAG_READONLY:
        assert "Readonly mode:" in instructions
    else:
        assert "Readonly mode:" not in instructions


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
#: meta hint. Read-only only — best-effort hint to Claude Code that
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
        "capture_pane",
        "capture_since",
        "snapshot_pane",
        "paste_text",
        "get_pane_info",
        "find_pane_by_position",
        "clear_pane",
        "search_panes",
        "wait_for_text",
        "wait_for_content_change",
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

    from libtmux_mcp.server import _lifespan

    def _missing_tmux(_name: str) -> None:
        return None

    monkeypatch.setattr("libtmux_mcp.server.shutil.which", _missing_tmux)

    async def _enter() -> None:
        async with _lifespan(_app=None):  # type: ignore[arg-type]
            pytest.fail("lifespan should have raised before yielding")

    with pytest.raises(RuntimeError, match="tmux binary not found"):
        asyncio.run(_enter())


def test_lifespan_clears_server_cache_on_exit() -> None:
    """Clean lifespan exit empties the process-wide ``_server_cache``."""
    import asyncio

    from libtmux_mcp._utils import _server_cache
    from libtmux_mcp.server import _lifespan

    # Seed the cache with a sentinel entry — the actual value doesn't
    # matter; we're checking that exit clears it.
    _server_cache[("sentinel_socket", None, None)] = t.cast("t.Any", object())

    async def _cycle() -> None:
        async with _lifespan(_app=None):  # type: ignore[arg-type]
            # While the lifespan is active the cache still holds state.
            assert _server_cache

    asyncio.run(_cycle())
    assert _server_cache == {}


def test_server_constructed_with_lifespan() -> None:
    """The production FastMCP instance is wired with ``_lifespan``."""
    from libtmux_mcp.server import _lifespan, mcp

    assert mcp._lifespan is _lifespan


@pytest.mark.usefixtures("mcp_session")
def test_gc_mcp_buffers_deletes_mcp_prefixed_and_spares_others(
    mcp_server: Server,
) -> None:
    """``_gc_mcp_buffers`` deletes ``libtmux_mcp_*`` buffers only.

    Best-effort lifespan GC for leaked paste buffers — agents are
    supposed to ``delete_buffer`` after use, but an interrupted call
    chain can leak. The GC must NEVER touch non-MCP buffers (OS
    clipboard sync, user-authored buffers) — those are the human user's
    content.
    """
    from libtmux_mcp.server import _gc_mcp_buffers
    from libtmux_mcp.tools.buffer_tools import load_buffer

    # Seed: one MCP-owned buffer via the canonical path, one human-owned
    # buffer directly via tmux so it is outside the MCP prefix.
    ref = load_buffer(
        content="agent-staged",
        logical_name="leaky",
        socket_name=mcp_server.socket_name,
    )
    mcp_server.cmd("set-buffer", "-b", "human_buffer", "user-content")

    names_before = mcp_server.cmd("list-buffers", "-F", "#{buffer_name}").stdout
    assert ref.buffer_name in names_before
    assert "human_buffer" in names_before

    _gc_mcp_buffers({(mcp_server.socket_name, None, None): mcp_server})

    names_after = mcp_server.cmd("list-buffers", "-F", "#{buffer_name}").stdout
    assert ref.buffer_name not in names_after, "GC must delete MCP-namespaced buffers"
    assert "human_buffer" in names_after, "GC must not touch non-MCP buffers"

    # Clean up the human buffer so the fixture teardown stays tidy.
    mcp_server.cmd("delete-buffer", "-b", "human_buffer")


def test_gc_mcp_buffers_swallows_errors() -> None:
    """GC logs but never raises when tmux is unreachable."""
    from libtmux_mcp.server import _gc_mcp_buffers

    class _BrokenServer:
        def cmd(self, *_a: object, **_kw: object) -> object:
            msg = "tmux is dead"
            raise RuntimeError(msg)

    # Must not raise — lifespan shutdown cannot tolerate exceptions here.
    # Cast is needed because _BrokenServer only implements ``cmd``; the
    # real cache stores full Server instances, but GC is best-effort and
    # consumes only the ``cmd`` method so a partial stub is sufficient.
    _gc_mcp_buffers(
        t.cast(
            "t.Mapping[_ServerCacheKey, t.Any]",
            {(None, None, None): _BrokenServer()},
        )
    )
