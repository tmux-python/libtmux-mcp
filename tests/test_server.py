"""Tests for libtmux MCP server configuration."""

from __future__ import annotations

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


class CardContract(t.NamedTuple):
    """Contract about what ``_BASE_INSTRUCTIONS`` must / must not contain.

    The slim card is the public-facing server prompt — every MCP client
    that connects gets it. ``must_include`` pins the substrings agents
    rely on to orient (server identity, socket_name exception, three
    handles); ``must_exclude`` pins the deleted-pre-refactor phrasing so
    a future drift back to the lie ("All tools accept socket_name") fails
    loudly here instead of silently shipping.
    """

    test_id: str
    must_include: tuple[str, ...]
    must_exclude: tuple[str, ...] = ()


CARD_CONTRACTS: list[CardContract] = [
    CardContract(
        test_id="server_identity",
        must_include=(
            "tmux hierarchy",
            "Server > Session > Window > Pane",
            "pane_id",
        ),
    ),
    CardContract(
        test_id="socket_name_exception",
        # ``list_servers`` does NOT accept socket_name (it's the discovery
        # tool — see ``server_tools.py`` SOCKET_NAME_EXEMPT). The pre-refactor
        # wording "All tools accept socket_name" was a lie; the new card
        # qualifies "Targeted tools" and names list_servers explicitly.
        must_include=("Targeted tools", "list_servers", "extra_socket_paths"),
        must_exclude=("All tools accept",),
    ),
    CardContract(
        test_id="three_handles",
        # The card's job is to point at where the rest of the answer lives
        # (tools / resources / prompts), not to inline tool-specific rules.
        must_include=("Tools", "Resources (tmux://)", "Prompts"),
    ),
]


@pytest.mark.parametrize(
    CardContract._fields,
    CARD_CONTRACTS,
    ids=[c.test_id for c in CARD_CONTRACTS],
)
def test_card_contracts(
    test_id: str,
    must_include: tuple[str, ...],
    must_exclude: tuple[str, ...],
) -> None:
    """``_BASE_INSTRUCTIONS`` is the slim "three handles" server card.

    Tool-specific rules live in tool descriptions — Phase 1 of the
    instructions slim-down moved them there. The card carries only
    cross-cutting orientation: server identity, the socket_name
    exception, and pointers to the Tools / Resources / Prompts handles.
    Anything naming a specific tool's preference rule belongs at the
    call site, not here.
    """
    for needle in must_include:
        assert needle in _BASE_INSTRUCTIONS, (
            f"[{test_id}] missing required substring {needle!r}"
        )
    for needle in must_exclude:
        assert needle not in _BASE_INSTRUCTIONS, (
            f"[{test_id}] forbidden substring {needle!r} crept back in"
        )


def test_card_length_budget() -> None:
    """``_BASE_INSTRUCTIONS`` stays under the ~200-word budget.

    Per-tool rules belong in tool descriptions (visible at every
    ``list_tools`` call), not in this card. This guard fails loudly if a
    future contributor reaches for the card to add a tool-specific rule,
    pointing them at the right home before the card grows back into the
    305-word monolith it just shrank from.
    """
    word_count = len(_BASE_INSTRUCTIONS.split())
    assert word_count <= 200, (
        f"_BASE_INSTRUCTIONS grew to {word_count} words; per-tool rules "
        f"belong in tool descriptions, not the card. See the module-level "
        f"comment in server.py for the boundary."
    )


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


@pytest.mark.parametrize(
    ("tool_name", "must_include"),
    [
        ("capture_pane", "snapshot_pane"),
        ("capture_pane", "wait_for_text"),
        ("capture_pane", "search_panes"),
        ("show_hooks", "tmux config file"),
        ("load_buffer", "list_buffers"),
        ("load_buffer", "clipboard history"),
        ("send_keys", "wait_for_text"),
        ("list_panes", "search_panes"),
        ("list_windows", "search_panes"),
    ],
)
def test_tool_description_includes(tool_name: str, must_include: str) -> None:
    """Tool descriptions carry cross-references the agent needs at the call site.

    Phase 1 of the BASE_INSTRUCTIONS slim-down: rules that are tool-specific
    live in tool descriptions (surfaced by FastMCP at every ``list_tools``
    call), not in the global card or in module docstrings (which FastMCP
    does not surface). The asserted phrases are the ones an agent would
    look for when deciding which tool to call:

    * ``capture_pane`` cross-references richer alternatives
      (``snapshot_pane``, ``wait_for_text``) and the parallel-search tool
      (``search_panes``).
    * ``show_hooks`` carries the no-set_hook rationale ("tmux config
      file") that previously lived only in ``hook_tools``' module
      docstring.
    * ``load_buffer`` carries the no-list_buffers / clipboard-privacy
      rationale that previously lived only in ``buffer_tools``' module
      docstring.
    * ``send_keys`` points at ``wait_for_text`` instead of a poll loop.
    * ``list_panes`` / ``list_windows`` point at ``search_panes`` for
      content (vs. metadata-only) queries.

    The "tool exists" assertion is a strict upgrade over substring tests
    on ``_BASE_INSTRUCTIONS``: a future rename that drops the rule fails
    here instead of silently losing agent-relevant guidance.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import register_tools

    mcp = FastMCP(name="tool-description-contract")
    register_tools(mcp)

    tools = asyncio.run(mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert tool_name in by_name, f"{tool_name!r} is not registered"
    description = by_name[tool_name].description or ""
    assert must_include in description, (
        f"{tool_name!r} description missing {must_include!r}; got {description!r}"
    )


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
