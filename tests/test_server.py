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


def test_base_instructions_prefer_wait_over_poll() -> None:
    """_BASE_INSTRUCTIONS names wait_for_text and wait_for_content_change.

    The wait tools block server-side, which is dramatically cheaper in
    agent turns than ``capture_pane`` in a retry loop. Making them
    discoverable from the instructions is a no-cost UX win.
    """
    assert "wait_for_text" in _BASE_INSTRUCTIONS
    assert "wait_for_content_change" in _BASE_INSTRUCTIONS


def test_base_instructions_document_hook_boundary() -> None:
    """_BASE_INSTRUCTIONS explains hooks are read-only by design.

    Without this sentence agents waste a turn asking for ``set_hook`` or
    trying to write hooks through a nonexistent tool. Naming the
    boundary heads off the exploratory call.
    """
    assert "HOOKS ARE READ-ONLY" in _BASE_INSTRUCTIONS
    assert "show_hooks" in _BASE_INSTRUCTIONS
    assert "tmux config file" in _BASE_INSTRUCTIONS


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
