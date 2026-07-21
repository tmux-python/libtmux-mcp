"""Tests for libtmux-mcp prompt surface."""

from __future__ import annotations

import ast
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
    """``run_and_wait`` prompt teaches the typed command primitive."""
    from libtmux_mcp.prompts.recipes import run_and_wait

    text = run_and_wait(command="pytest", pane_id="%1", timeout=30.0)
    assert "run_command" in text
    assert "exit_status" in text
    assert "timed_out" in text
    assert "output" in text
    assert "wait_for_channel" not in text
    assert "tmux wait-for -S" not in text
    assert "send_keys(" not in text
    assert "capture_pane(" not in text


def test_run_and_wait_does_not_render_manual_channel_recipe() -> None:
    """``run_and_wait`` leaves channel plumbing to ``run_command``."""
    from libtmux_mcp.prompts.recipes import run_and_wait

    text = run_and_wait(command="pytest", pane_id="%1")
    assert "libtmux_mcp_wait_" not in text
    assert "result = run_command(" in text


def test_run_and_wait_handles_quoted_commands() -> None:
    """Single quotes in the command don't corrupt the rendered call."""
    from libtmux_mcp.prompts.recipes import run_and_wait

    text = run_and_wait(command="python -c 'print(1)'", pane_id="%1")
    # Extract the ``command=`` argument as a Python literal and confirm
    # it parses back to a string containing the original command.
    command_line = next(line for line in text.splitlines() if "command=" in line)
    _, _, payload = command_line.partition("command=")
    payload = payload.rstrip(",").strip()
    parsed = ast.literal_eval(payload)
    assert isinstance(parsed, str)
    assert "python -c 'print(1)'" in parsed
    assert "suppress_history=" not in text


@pytest.mark.parametrize(
    ("command", "suppression_disabled"),
    [
        pytest.param("printf first\nprintf second", True, id="line-feed"),
        pytest.param("printf first\rprintf second", True, id="carriage-return"),
        pytest.param(r"printf first\nprintf second", False, id="escaped-line-feed"),
    ],
)
def test_run_and_wait_disables_suppression_for_multiline_commands(
    command: str,
    suppression_disabled: bool,
) -> None:
    """Multiline recipes remain executable and disclose history behavior."""
    from libtmux_mcp.prompts.recipes import run_and_wait

    text = run_and_wait(command=command, pane_id="%1")

    sample = text.split("```python\n", 1)[1].split("\n```", 1)[0]
    ast.parse(sample)
    assert f"command={command!r}," in text
    assert ("suppress_history=False," in text) is suppression_disabled
    assert ("may be recorded by the shell" in text) is suppression_disabled


def test_interrupt_gracefully_does_not_escalate() -> None:
    """``interrupt_gracefully`` refuses SIGQUIT auto-escalation."""
    from libtmux_mcp.prompts.recipes import interrupt_gracefully

    text = interrupt_gracefully(pane_id="%3")
    assert "do NOT escalate automatically" in text


def test_diagnose_failing_pane_uses_capture_since_for_repeated_reads() -> None:
    """Diagnosis recipe routes repeated observation to ``capture_since``."""
    from libtmux_mcp.prompts.recipes import diagnose_failing_pane

    text = diagnose_failing_pane(pane_id="%1")
    assert "snapshot_pane" in text
    assert "capture_since" in text
    assert "cursor" in text


def test_build_dev_workspace_does_not_deadlock_on_screen_grabbers() -> None:
    """``build_dev_workspace`` guides post-launch waits to content-change.

    The recipe must not tell agents to wait for a shell prompt after
    launching vim or a long-running tailing command.

    Regression guard: the earlier rewrite of this recipe preserved a
    stale "wait for the prompt between each step" line that would
    deadlock an agent following it literally — vim and ``watch`` /
    ``tail -f`` take over the terminal and never draw a shell prompt,
    so the wait would block until timeout.

    The corrected recipe uses ``wait_for_text(patterns=null)`` after
    launch for an optional "program started" confirmation — an
    any-new-output check that works for every shell and every program,
    no glyph matching required.
    """
    from libtmux_mcp.prompts.recipes import build_dev_workspace

    text = build_dev_workspace(session_name="dev")
    # The stale guidance must be gone.
    assert "wait for the prompt" not in text
    assert "Between each step, wait for the prompt" not in text
    # Post-launch confirmation still uses the right primitive:
    # any-new-output, not prompt-match.
    assert "patterns=null" in text


def test_build_dev_workspace_has_no_prompt_regex_or_stray_enter() -> None:
    r"""No shell-specific prompt regex, no ``send_keys(keys="")`` line.

    Regression guard for two residual ergonomic issues that F1's
    rewrite did not catch:

    * The literal regex ``\$ |\# |\% `` only matches default
      bash/zsh prompts; starship, oh-my-zsh, pure, p10k, fish all
      miss and would hang for 5s per pane.
    * ``send_keys(pane_id="%B", keys="")`` is not a no-op —
      libtmux's ``Pane.send_keys`` sends an Enter keystroke when
      ``enter=True`` (the default). The comment "(leave the shell
      idle)" actively misled readers.

    Both lines were deleted; this test pins that removal.
    """
    from libtmux_mcp.prompts.recipes import build_dev_workspace

    text = build_dev_workspace(session_name="dev")
    # Shell-specific prompt glyph regex must be gone.
    assert r"\$ |\# |\% " not in text
    # The stray-Enter-into-idle-shell line must be gone.
    assert 'send_keys(pane_id="%B", keys="")' not in text
    # Positive pin: post-launch UI confirmation is still available
    # via the shell-agnostic any-new-output primitive.
    assert "patterns=null" in text


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
