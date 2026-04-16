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
    is ``libtmux_mcp_wait_<uuid4hex>`` (full 128-bit UUID, fresh per
    invocation) and consistent within one invocation — the name that
    appears in the ``send_keys`` payload must match the
    ``wait_for_channel`` call.
    """
    import re

    from libtmux_mcp.prompts.recipes import run_and_wait

    first = run_and_wait(command="pytest", pane_id="%1")
    second = run_and_wait(command="pytest", pane_id="%1")

    pattern = re.compile(r"libtmux_mcp_wait_[0-9a-f]{32}")
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


def test_build_dev_workspace_does_not_deadlock_on_screen_grabbers() -> None:
    """``build_dev_workspace`` guides post-launch waits to content-change.

    The recipe must not tell agents to wait for a shell prompt after
    launching vim or a long-running tailing command.

    Regression guard: the earlier rewrite of this recipe preserved a
    stale "wait for the prompt between each step" line that would
    deadlock an agent following it literally — vim and ``watch`` /
    ``tail -f`` take over the terminal and never draw a shell prompt,
    so the wait would block until timeout.

    The corrected recipe uses ``wait_for_content_change`` after launch
    for an optional "program started" confirmation — a screen-change
    check that works for every shell and every program, no glyph
    matching required.
    """
    from libtmux_mcp.prompts.recipes import build_dev_workspace

    text = build_dev_workspace(session_name="dev")
    # The stale guidance must be gone.
    assert "wait for the prompt" not in text
    assert "Between each step, wait for the prompt" not in text
    # Post-launch confirmation still uses the right primitive:
    # content-change, not prompt-match.
    assert "wait_for_content_change" in text


def _extract_tool_calls(
    rendered: str, tool_names: set[str]
) -> list[tuple[str, list[str]]]:
    """Extract ``tool_name(kw=..., ...)`` call sites from prompt text.

    Walks the rendered prompt, finds each token that matches a known
    tool name followed by ``(``, paren-matches with string-awareness,
    and parses the slice via :mod:`ast`. Returns ``(tool_name, kwnames)``
    tuples for every successfully parsed call. Parse failures are
    silently skipped because prompts intentionally contain prose
    snippets that may resemble calls (e.g. ``split_window(target=pane A)``
    when that is being described but not executed).
    """
    import ast
    import warnings

    results: list[tuple[str, list[str]]] = []
    i = 0
    n = len(rendered)
    while i < n:
        for name in tool_names:
            end = i + len(name)
            prev_is_ident = i > 0 and (
                rendered[i - 1].isalnum() or rendered[i - 1] == "_"
            )
            if (
                rendered[i:end] == name
                and not prev_is_ident
                and end < n
                and rendered[end] == "("
            ):
                # Paren-match with quote awareness.
                depth = 0
                j = end
                quote: str | None = None
                while j < n:
                    c = rendered[j]
                    if quote is not None:
                        if c == "\\":
                            j += 2
                            continue
                        if c == quote:
                            quote = None
                    elif c in ("'", '"'):
                        quote = c
                    elif c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                if depth != 0:
                    break
                snippet = rendered[i : j + 1]
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", SyntaxWarning)
                        tree = ast.parse(snippet, mode="eval")
                except SyntaxError:
                    break
                if isinstance(tree.body, ast.Call) and isinstance(
                    tree.body.func, ast.Name
                ):
                    results.append(
                        (
                            tree.body.func.id,
                            [kw.arg for kw in tree.body.keywords if kw.arg],
                        )
                    )
                i = j + 1
                break
        else:
            i += 1
            continue
    return results


@pytest.mark.parametrize(
    ("recipe_name", "kwargs"),
    [
        ("run_and_wait", {"command": "pytest", "pane_id": "%1"}),
        ("diagnose_failing_pane", {"pane_id": "%1"}),
        ("build_dev_workspace", {"session_name": "dev"}),
        ("interrupt_gracefully", {"pane_id": "%1"}),
    ],
)
def test_every_prompt_recipe_returns_str(
    recipe_name: str, kwargs: dict[str, object]
) -> None:
    """Every prompt recipe returns a non-empty ``str``.

    Regression guard mirroring the read-heavy tool shape tests in
    ``tests/test_server_tools.py`` and ``tests/test_pane_tools.py``:
    prompts must return a string template so MCP ``get_prompt`` calls
    return a usable body. A future refactor that accidentally returns
    a Pydantic model, dict, or ``None`` would silently break the MCP
    prompt surface; this parametrized test fails loudly.
    """
    from libtmux_mcp.prompts import recipes

    fn = getattr(recipes, recipe_name)
    result = fn(**kwargs)
    assert isinstance(result, str)
    assert result, f"{recipe_name} returned an empty string"


def test_prompt_tool_calls_match_real_signatures() -> None:
    """Every ``tool_name(param=...)`` in every prompt matches a real signature.

    Regression guard for the ``build_dev_workspace`` drift where the
    prompt told clients to call ``create_session(name=...)`` while the
    real parameter is ``session_name`` — a failure mode that makes the
    "discover via prompt" workflow actively misleading.

    The test renders each recipe with plausible sample arguments,
    extracts every ``known_tool(...)`` call from the output, and
    asserts each keyword argument is a real parameter on the matching
    Python tool function. Uses :func:`inspect.signature` against the
    raw tool functions as the source of truth.
    """
    import inspect

    from libtmux_mcp.prompts import recipes as recipes_mod
    from libtmux_mcp.tools import (
        buffer_tools,
        hook_tools,
        option_tools,
        pane_tools,
        server_tools,
        session_tools,
        wait_for_tools,
        window_tools,
    )

    modules = (
        buffer_tools,
        hook_tools,
        option_tools,
        pane_tools,
        server_tools,
        session_tools,
        wait_for_tools,
        window_tools,
    )
    tool_params: dict[str, set[str]] = {}
    for mod in modules:
        for name, obj in vars(mod).items():
            if name.startswith("_") or not callable(obj):
                continue
            try:
                sig = inspect.signature(obj)
            except (TypeError, ValueError):
                continue
            # handle_tool_errors wraps tools but preserves __wrapped__.
            fn = getattr(obj, "__wrapped__", obj)
            try:
                sig = inspect.signature(fn)
            except (TypeError, ValueError):
                continue
            params = {p.name for p in sig.parameters.values() if p.name != "ctx"}
            if params:
                tool_params.setdefault(name, set()).update(params)

    samples: list[tuple[str, dict[str, object]]] = [
        ("run_and_wait", {"command": "pytest", "pane_id": "%1", "timeout": 30.0}),
        ("diagnose_failing_pane", {"pane_id": "%1"}),
        ("build_dev_workspace", {"session_name": "dev"}),
        ("interrupt_gracefully", {"pane_id": "%1"}),
    ]
    for recipe_name, kwargs in samples:
        fn = getattr(recipes_mod, recipe_name)
        rendered = fn(**kwargs)
        calls = _extract_tool_calls(rendered, set(tool_params))
        assert calls, (
            f"no tool calls extracted from {recipe_name!r} — either the "
            f"prompt body changed drastically or the extractor regressed"
        )
        for tool_name, kw_names in calls:
            valid = tool_params[tool_name]
            for kw in kw_names:
                assert kw in valid, (
                    f"{recipe_name}: {tool_name}({kw}=...) is invalid — "
                    f"{tool_name} accepts {sorted(valid)}"
                )
