"""Tests for semantic shell-history suppression policy."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import pathlib
import shlex
import subprocess
import sys
import textwrap
import typing as t

import pytest
from fastmcp import Client, FastMCP
from libtmux.test.retry import retry_until

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server


class SuppressHistorySettingFixture(t.NamedTuple):
    """Fixture for valid ``LIBTMUX_SUPPRESS_HISTORY`` values."""

    test_id: str
    value: str | None
    expected: bool


SUPPRESS_HISTORY_SETTING_FIXTURES = [
    SuppressHistorySettingFixture("unset", None, True),
    SuppressHistorySettingFixture("disabled", "0", False),
    SuppressHistorySettingFixture("enabled", "1", True),
]


class McpHistoryBehaviorFixture(t.NamedTuple):
    """Fixture for effective MCP command behavior."""

    test_id: str
    value: str
    omitted_is_saved: bool


MCP_HISTORY_BEHAVIOR_FIXTURES = [
    McpHistoryBehaviorFixture("startup_disabled", "0", True),
    McpHistoryBehaviorFixture("startup_enabled", "1", False),
]

SPAWN_SUPPRESS_PERSISTENT_HISTORY_DESCRIPTION = (
    "Whether to suppress persistent history for the spawned shell. Defaults "
    "to False for MCP and direct Python calls. This per-call option does not "
    "inherit LIBTMUX_SUPPRESS_HISTORY. Startup files may override these "
    "controls."
)


def _history_server(value: str) -> FastMCP:
    """Build a focused MCP server with one resolved history transform."""
    from libtmux_mcp._history import (
        _configure_history_defaults,
        _resolve_suppress_history,
    )
    from libtmux_mcp.tools import batch_tools, buffer_tools, pane_tools

    mcp = FastMCP(f"history-{value}")
    batch_tools.register(mcp)
    buffer_tools.register(mcp)
    pane_tools.register(mcp)
    _configure_history_defaults(mcp, _resolve_suppress_history(value))
    return mcp


def _assert_run_command_succeeded(result: t.Any) -> None:
    """Assert a semantic command completed instead of merely dispatching."""
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["timed_out"] is False
    assert result.structured_content["exit_status"] == 0


@pytest.mark.parametrize(
    SuppressHistorySettingFixture._fields,
    SUPPRESS_HISTORY_SETTING_FIXTURES,
    ids=[fixture.test_id for fixture in SUPPRESS_HISTORY_SETTING_FIXTURES],
)
def test_resolve_suppress_history_setting(
    test_id: str,
    value: str | None,
    expected: bool,
) -> None:
    """The startup setting accepts unset, ``0``, and ``1``."""
    from libtmux_mcp._history import _resolve_suppress_history

    assert test_id
    assert _resolve_suppress_history(value) is expected


def test_resolve_suppress_history_rejects_invalid_without_echoing_value() -> None:
    """Invalid startup input raises fixed, non-reflective guidance."""
    from libtmux_mcp._history import _resolve_suppress_history

    rejected = "private-invalid-setting"
    expected = "LIBTMUX_SUPPRESS_HISTORY must be unset, '0', or '1'"

    with pytest.raises(ValueError) as excinfo:
        _resolve_suppress_history(rejected)

    assert str(excinfo.value) == expected
    assert rejected not in str(excinfo.value)


def test_history_transform_changes_exact_semantic_tool_set() -> None:
    """Only run-command inherits the command-history boolean default."""
    from libtmux_mcp._history import _configure_history_defaults
    from libtmux_mcp.tools import register_tools

    command_tools = {"run_command"}
    spawn_tools = {
        "create_session",
        "create_window",
        "split_window",
        "respawn_pane",
    }
    raw_tools = {"send_keys"}
    history_tools = command_tools | raw_tools

    async def _schemas(enabled: bool) -> dict[str, dict[str, t.Any]]:
        mcp = FastMCP(f"history-transform-{enabled}")
        register_tools(mcp)
        _configure_history_defaults(mcp, enabled)
        async with Client(mcp) as client:
            tools = await client.list_tools()
        schemas: dict[str, dict[str, t.Any]] = {}
        for tool in tools:
            properties = tool.inputSchema["properties"]
            if tool.name in spawn_tools:
                assert "suppress_history" not in properties
                schemas[tool.name] = properties["suppress_persistent_history"]
            elif "suppress_history" in properties:
                schemas[tool.name] = properties["suppress_history"]
        return schemas

    disabled = asyncio.run(_schemas(False))
    enabled = asyncio.run(_schemas(True))
    changed = {
        name
        for name, schema in enabled.items()
        if schema["default"] != disabled[name]["default"]
    }

    assert set(disabled) == history_tools | spawn_tools
    assert set(enabled) == history_tools | spawn_tools
    assert changed == command_tools
    for default, schemas in ((False, disabled), (True, enabled)):
        for name in history_tools | spawn_tools:
            schema = schemas[name]
            assert schema["type"] == "boolean"
            expected = default if name in command_tools else False
            assert schema["default"] is expected
            assert "anyOf" not in schema
            if name in spawn_tools:
                description = " ".join(schema["description"].split())
                assert description == SPAWN_SUPPRESS_PERSISTENT_HISTORY_DESCRIPTION


class SpawnEnvironmentFixture(t.NamedTuple):
    """Fixture for successful spawn-environment preparation."""

    test_id: str
    environment: dict[str, str] | str | None
    suppress_persistent_history: bool
    expected: dict[str, str] | None


SPAWN_ENVIRONMENT_FIXTURES = [
    SpawnEnvironmentFixture("none_disabled", None, False, None),
    SpawnEnvironmentFixture("empty_disabled", {}, False, {}),
    SpawnEnvironmentFixture("dict_copied", {"FOO": "bar"}, False, {"FOO": "bar"}),
    SpawnEnvironmentFixture("json_normalized", '{"FOO":"bar"}', False, {"FOO": "bar"}),
    SpawnEnvironmentFixture(
        "enabled_defaults",
        None,
        True,
        {
            "HISTFILE": "",
            "HISTCONTROL": "ignorespace",
            "fish_private_mode": "1",
            "fish_history": "",
        },
    ),
    SpawnEnvironmentFixture(
        "history_control_merged",
        {"FOO": "bar", "HISTCONTROL": "ignoredups"},
        True,
        {
            "FOO": "bar",
            "HISTFILE": "",
            "HISTCONTROL": "ignoredups:ignorespace",
            "fish_private_mode": "1",
            "fish_history": "",
        },
    ),
    SpawnEnvironmentFixture(
        "history_control_ignorespace_preserved",
        {"HISTCONTROL": "erasedups:ignorespace"},
        True,
        {
            "HISTFILE": "",
            "HISTCONTROL": "erasedups:ignorespace",
            "fish_private_mode": "1",
            "fish_history": "",
        },
    ),
    SpawnEnvironmentFixture(
        "history_control_ignoreboth_preserved",
        {"HISTCONTROL": "ignoreboth"},
        True,
        {
            "HISTFILE": "",
            "HISTCONTROL": "ignoreboth",
            "fish_private_mode": "1",
            "fish_history": "",
        },
    ),
]


@pytest.mark.parametrize(
    SpawnEnvironmentFixture._fields,
    SPAWN_ENVIRONMENT_FIXTURES,
    ids=[fixture.test_id for fixture in SPAWN_ENVIRONMENT_FIXTURES],
)
def test_prepare_spawn_environment_normalizes_copies_and_merges(
    test_id: str,
    environment: dict[str, str] | str | None,
    suppress_persistent_history: bool,
    expected: dict[str, str] | None,
) -> None:
    """Spawn environments are normalized without modifying caller input."""
    from libtmux_mcp._history import _prepare_spawn_environment

    assert test_id
    original = environment.copy() if isinstance(environment, dict) else environment
    result = _prepare_spawn_environment(
        environment,
        suppress_persistent_history=suppress_persistent_history,
    )

    assert result == expected
    if isinstance(environment, dict):
        assert environment == original
        if result is not None:
            assert result is not environment


@pytest.mark.parametrize(
    ("name", "supplied", "correction"),
    [
        (
            "HISTFILE",
            "private-bash-history-path",
            "omit it or set it to an empty string",
        ),
        (
            "fish_history",
            "private-fish-history-name",
            "omit it or set it to an empty string",
        ),
        (
            "fish_private_mode",
            "private-fish-mode-value",
            "omit it or set it to '1'",
        ),
    ],
)
def test_prepare_spawn_environment_rejects_conflicts_without_values(
    name: str,
    supplied: str,
    correction: str,
) -> None:
    """Policy conflicts identify only the variable, never its supplied value."""
    from fastmcp.exceptions import ToolError

    from libtmux_mcp._history import _prepare_spawn_environment

    environment = {"UNCHANGED": "caller", name: supplied}
    original = environment.copy()
    expected = (
        f"environment variable {name} conflicts with "
        "suppress_persistent_history=True; "
        f"{correction}"
    )

    with pytest.raises(ToolError) as excinfo:
        _prepare_spawn_environment(environment, suppress_persistent_history=True)

    assert str(excinfo.value) == expected
    assert supplied not in str(excinfo.value)
    assert environment == original


@pytest.mark.parametrize(
    "environment",
    [
        t.cast("t.Any", {1: "value"}),
        t.cast("t.Any", {"NAME": 1}),
        '{"NAME":1}',
    ],
    ids=["non_string_key", "non_string_value", "json_non_string_value"],
)
def test_prepare_spawn_environment_rejects_non_string_items(
    environment: dict[str, str] | str,
) -> None:
    """Tmux environment keys and values must both be strings."""
    from fastmcp.exceptions import ToolError

    from libtmux_mcp._history import _prepare_spawn_environment

    original = environment.copy() if isinstance(environment, dict) else environment
    with pytest.raises(ToolError) as excinfo:
        _prepare_spawn_environment(environment, suppress_persistent_history=False)

    assert str(excinfo.value) == "environment keys and values must be strings"
    if isinstance(environment, dict):
        assert environment == original


@pytest.mark.parametrize(
    SuppressHistorySettingFixture._fields,
    SUPPRESS_HISTORY_SETTING_FIXTURES,
    ids=[fixture.test_id for fixture in SUPPRESS_HISTORY_SETTING_FIXTURES],
)
def test_production_mcp_schema_scopes_startup_default_to_run_command(
    test_id: str,
    value: str | None,
    expected: bool,
) -> None:
    """Startup configuration never changes persistent-history spawn defaults."""
    script = textwrap.dedent(
        """
        import asyncio
        import json
        import logging

        from fastmcp import Client
        from libtmux_mcp.server import build_mcp_server

        logging.disable(logging.CRITICAL)

        async def main():
            first = build_mcp_server()
            before = len(first.transforms)
            second = build_mcp_server()
            after = len(second.transforms)
            async with Client(first) as client:
                tools = {tool.name: tool for tool in await client.list_tools()}
            tool = tools["run_command"]
            schema = tool.inputSchema["properties"]["suppress_history"]
            print(json.dumps({
                "same_server": first is second,
                "transform_counts": [before, after],
                "schema": schema,
                "annotations": tool.annotations.model_dump(
                    mode="json", exclude_none=True
                ),
                "tags": tool.meta["fastmcp"]["tags"],
                "raw_defaults": {
                    "send_keys": tools["send_keys"].inputSchema["properties"]
                    ["suppress_history"]["default"],
                    "send_keys_batch": tools["send_keys_batch"].inputSchema
                    ["properties"]["operations"]["items"]["properties"]
                    ["suppress_history"]["default"],
                },
                "spawn_defaults": {
                    name: tools[name].inputSchema["properties"]
                    ["suppress_persistent_history"]["default"]
                    for name in (
                        "create_session",
                        "create_window",
                        "split_window",
                        "respawn_pane",
                    )
                },
                "spawn_descriptions": {
                    name: tools[name].inputSchema["properties"]
                    ["suppress_persistent_history"]["description"]
                    for name in (
                        "create_session",
                        "create_window",
                        "split_window",
                        "respawn_pane",
                    )
                },
            }, sort_keys=True))

        asyncio.run(main())
        """
    )
    env = os.environ.copy()
    if value is None:
        env.pop("LIBTMUX_SUPPRESS_HISTORY", None)
    else:
        env["LIBTMUX_SUPPRESS_HISTORY"] = value

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    payload = json.loads(completed.stdout.splitlines()[-1])

    assert test_id
    assert payload["same_server"] is True
    assert payload["transform_counts"][0] == payload["transform_counts"][1]
    assert payload["schema"]["type"] == "boolean"
    assert payload["schema"]["default"] is expected
    assert "anyOf" not in payload["schema"]
    assert payload["annotations"] == {
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
        "readOnlyHint": False,
    }
    assert sorted(payload["tags"]) == ["mutating", "self-bounded"]
    assert payload["raw_defaults"] == {
        "send_keys": False,
        "send_keys_batch": False,
    }
    assert payload["spawn_defaults"] == {
        "create_session": False,
        "create_window": False,
        "split_window": False,
        "respawn_pane": False,
    }
    expected_descriptions = dict.fromkeys(
        ("create_session", "create_window", "split_window", "respawn_pane"),
        SPAWN_SUPPRESS_PERSISTENT_HISTORY_DESCRIPTION,
    )
    assert {
        name: " ".join(description.split())
        for name, description in payload["spawn_descriptions"].items()
    } == expected_descriptions


def test_invalid_history_setting_fails_server_startup_without_echoing_value() -> None:
    """Invalid explicit privacy configuration prevents server startup."""
    rejected = "private-invalid-setting"
    env = {**os.environ, "LIBTMUX_SUPPRESS_HISTORY": rejected}
    completed = subprocess.run(
        [sys.executable, "-c", "import libtmux_mcp.server"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert completed.returncode != 0
    assert "LIBTMUX_SUPPRESS_HISTORY must be unset, '0', or '1'" in completed.stderr
    assert rejected not in completed.stderr


def test_run_command_describes_mcp_precedence_and_direct_python_default() -> None:
    """The tool description distinguishes MCP omission from Python calls."""
    from libtmux_mcp.tools import pane_tools

    expected = (
        "For MCP calls, omission uses the server's "
        "LIBTMUX_SUPPRESS_HISTORY default; an explicit value overrides it. "
        "Direct Python calls default to False. Best effort: the shell must honor "
        "space-prefixed history suppression. Suppression requires a single-line "
        "command; multiline commands remain available when suppression is false."
    )
    parameter = inspect.signature(pane_tools.run_command).parameters["suppress_history"]
    mcp = FastMCP("run-command-description")
    pane_tools.register(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    schema = tools["run_command"].parameters["properties"]["suppress_history"]

    assert parameter.annotation == "bool"
    assert parameter.default is False
    assert " ".join(schema["description"].split()) == expected


@pytest.mark.parametrize(
    McpHistoryBehaviorFixture._fields,
    MCP_HISTORY_BEHAVIOR_FIXTURES,
    ids=[fixture.test_id for fixture in MCP_HISTORY_BEHAVIOR_FIXTURES],
)
def test_mcp_run_command_uses_effective_default_and_resists_batching(
    test_id: str,
    value: str,
    omitted_is_saved: bool,
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """Omission inherits through direct MCP calls; booleans win.

    ``run_command`` is registered ``self-bounded``, so the generic batch
    wrapper rejects it rather than inheriting anything: the nested call
    never reaches a shell, which is asserted here against the real
    server rather than a probe.
    """
    histfile = tmp_path / f"{test_id}.bash_history"
    mcp_pane.send_keys("exec bash --noprofile --norc", enter=True)
    retry_until(
        lambda: any("bash-" in line for line in mcp_pane.capture_pane()),
        2,
        raises=True,
    )
    setup = (
        f"HISTFILE={shlex.quote(str(histfile))}; "
        "HISTCONTROL=ignorespace; set -o history; history -c; history -w"
    )
    mcp_pane.send_keys(setup, enter=True)
    retry_until(histfile.exists, 2, raises=True)

    omitted = f"MCP_OMITTED_{test_id}"
    explicit_true = f"MCP_TRUE_{test_id}"
    explicit_false = f"MCP_FALSE_{test_id}"
    batch_omitted = f"MCP_BATCH_OMITTED_{test_id}"
    flushed_marker = f"MCP_FLUSHED_{test_id}"
    omitted_output = tmp_path / f"{test_id}-omitted.txt"
    explicit_true_output = tmp_path / f"{test_id}-true.txt"
    explicit_false_output = tmp_path / f"{test_id}-false.txt"
    batch_output = tmp_path / f"{test_id}-batch.txt"
    flushed_output = tmp_path / f"{test_id}-flushed.txt"
    base = {
        "pane_id": mcp_pane.pane_id,
        "timeout": 3.0,
        "socket_name": mcp_server.socket_name,
    }

    def _write_command(marker: str, output: pathlib.Path) -> str:
        return f"printf '%s\\n' {shlex.quote(marker)} > {shlex.quote(str(output))}"

    async def _exercise() -> None:
        async with Client(_history_server(value)) as client:
            calls = (
                (
                    {**base, "command": _write_command(omitted, omitted_output)},
                    omitted_output,
                    omitted,
                ),
                (
                    {
                        **base,
                        "command": _write_command(
                            explicit_true,
                            explicit_true_output,
                        ),
                        "suppress_history": True,
                    },
                    explicit_true_output,
                    explicit_true,
                ),
                (
                    {
                        **base,
                        "command": _write_command(
                            explicit_false,
                            explicit_false_output,
                        ),
                        "suppress_history": False,
                    },
                    explicit_false_output,
                    explicit_false,
                ),
            )
            for arguments, output, marker in calls:
                result = await client.call_tool(
                    "run_command",
                    arguments,
                    raise_on_error=False,
                )
                _assert_run_command_succeeded(result)
                assert output.read_text() == f"{marker}\n"

            batch = await client.call_tool(
                "call_mutating_tools_batch",
                {
                    "operations": [
                        {
                            "tool": "run_command",
                            "arguments": {
                                **base,
                                "command": _write_command(
                                    batch_omitted,
                                    batch_output,
                                ),
                            },
                        }
                    ]
                },
                raise_on_error=False,
            )
            assert batch.is_error is False
            assert batch.structured_content is not None
            assert batch.structured_content["succeeded"] == 0
            assert batch.structured_content["failed"] == 1
            [operation] = batch.structured_content["results"]
            assert operation["success"] is False
            assert "cannot be batched" in operation["error"]
            assert not batch_output.exists()

            flushed = await client.call_tool(
                "run_command",
                {
                    **base,
                    "command": (
                        f"history -w; {_write_command(flushed_marker, flushed_output)}"
                    ),
                    "suppress_history": True,
                },
                raise_on_error=False,
            )
            _assert_run_command_succeeded(flushed)
            assert flushed_output.read_text() == f"{flushed_marker}\n"

    asyncio.run(_exercise())
    saved = histfile.read_text()

    assert (omitted in saved) is omitted_is_saved
    assert explicit_true not in saved
    assert explicit_false in saved
    assert batch_omitted not in saved


def test_global_history_transform_keeps_raw_and_paste_schemas_explicit_only() -> None:
    """Raw input defaults stay false and paste tools gain no policy argument."""

    async def _list_tools() -> dict[str, t.Any]:
        async with Client(_history_server("1")) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = asyncio.run(_list_tools())
    run_command = tools["run_command"].inputSchema["properties"]
    send_keys = tools["send_keys"].inputSchema["properties"]
    operation = tools["send_keys_batch"].inputSchema["properties"]["operations"][
        "items"
    ]["properties"]

    assert run_command["suppress_history"]["type"] == "boolean"
    assert run_command["suppress_history"]["default"] is True
    assert "anyOf" not in run_command["suppress_history"]
    assert send_keys["suppress_history"]["default"] is False
    assert operation["suppress_history"]["default"] is False
    assert "suppress_history" not in tools["paste_text"].inputSchema["properties"]
    assert "suppress_history" not in tools["paste_buffer"].inputSchema["properties"]


def test_global_history_default_leaves_raw_send_keys_bytes_and_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control/TUI input stays exact; explicit suppression adds one space.

    A fake pane delegates to libtmux's real ``send_keys`` at the pre-PTY
    command boundary, where an inherited prefix or merged Enter is observable.
    """
    from libtmux import Pane

    from libtmux_mcp.tools.pane_tools import io

    calls: list[tuple[str, tuple[str, ...]]] = []

    class FakeServer:
        tmux_bin = "tmux"
        socket_name = None
        socket_path = None

    class FakePane:
        pane_id = "%1"
        server = FakeServer()

        def cmd(self, *args: str) -> None:
            calls.append(("cmd", args))

        def enter(self) -> None:
            calls.append(("enter", ()))

        def send_keys(self, keys: str, **kwargs: t.Any) -> None:
            Pane.send_keys(t.cast("Pane", self), keys, **kwargs)

    pane = FakePane()
    monkeypatch.setattr(io, "_get_server", lambda **kwargs: FakeServer())
    monkeypatch.setattr(io, "_resolve_pane", lambda *args, **kwargs: pane)

    async def _exercise() -> None:
        async with Client(_history_server("1")) as client:
            requests = (
                {"keys": "C-c", "enter": False},
                {"keys": "partial-TUI", "enter": False, "literal": True},
                {"keys": "/needle", "enter": True},
                {
                    "keys": "explicit-secret",
                    "enter": True,
                    "literal": True,
                    "suppress_history": True,
                },
            )
            for arguments in requests:
                result = await client.call_tool(
                    "send_keys",
                    arguments,
                    raise_on_error=False,
                )
                assert result.is_error is False

    asyncio.run(_exercise())

    assert calls == [
        ("cmd", ("send-keys", "C-c")),
        ("cmd", ("send-keys", "-l", "partial-TUI")),
        ("cmd", ("send-keys", "/needle")),
        ("enter", ()),
        ("cmd", ("send-keys", "-l", " explicit-secret")),
        ("enter", ()),
    ]


def test_global_history_default_leaves_untimed_batch_operations_explicit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untimed batches preserve raw defaults, literal mode, and Enter.

    A fake pane exercises libtmux's real ``send_keys`` at the pre-PTY command
    boundary so exact literal bytes and the separate Enter call remain visible.
    """
    from libtmux import Pane

    from libtmux_mcp.tools.pane_tools import io

    calls: list[tuple[str, tuple[str, ...]]] = []

    class FakeServer:
        tmux_bin = "tmux"
        socket_name = None
        socket_path = None

    class FakePane:
        pane_id = "%1"
        server = FakeServer()

        def cmd(self, *args: str) -> None:
            calls.append(("cmd", args))

        def enter(self) -> None:
            calls.append(("enter", ()))

        def send_keys(self, keys: str, **kwargs: t.Any) -> None:
            Pane.send_keys(t.cast("Pane", self), keys, **kwargs)

    pane = FakePane()
    monkeypatch.setattr(io, "_get_server", lambda **kwargs: FakeServer())
    monkeypatch.setattr(io, "_resolve_pane", lambda *args, **kwargs: pane)

    async def _exercise() -> None:
        async with Client(_history_server("1")) as client:
            result = await client.call_tool(
                "send_keys_batch",
                {
                    "operations": [
                        {"keys": "C-c", "enter": False},
                        {
                            "keys": "TUI_BATCH_DEFAULT",
                            "enter": True,
                            "literal": True,
                        },
                        {
                            "keys": "batch-secret",
                            "enter": True,
                            "literal": True,
                            "suppress_history": True,
                        },
                    ]
                },
                raise_on_error=False,
            )
            assert result.is_error is False

    asyncio.run(_exercise())

    assert calls == [
        ("cmd", ("send-keys", "C-c")),
        ("cmd", ("send-keys", "-l", "TUI_BATCH_DEFAULT")),
        ("enter", ()),
        ("cmd", ("send-keys", "-l", " batch-secret")),
        ("enter", ()),
    ]


def test_global_history_default_leaves_timed_batch_operations_explicit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timed batches preserve raw bytes and send Enter separately.

    Timed batches bypass ``Pane.send_keys``, so subprocess interception at the
    pre-PTY argv boundary is required to expose prefixes and Enter coalescing.
    """
    from libtmux_mcp.tools.pane_tools import io

    calls: list[list[str]] = []

    class FakeServer:
        tmux_bin = "tmux"
        socket_name = None
        socket_path = None

    class FakePane:
        pane_id = "%1"
        server = FakeServer()

    def _run(argv: list[str], **kwargs: t.Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    pane = FakePane()
    monkeypatch.setattr(io, "_get_server", lambda **kwargs: FakeServer())
    monkeypatch.setattr(io, "_resolve_pane", lambda *args, **kwargs: pane)
    monkeypatch.setattr("libtmux_mcp.tools.pane_tools.io.subprocess.run", _run)

    async def _exercise() -> None:
        async with Client(_history_server("1")) as client:
            result = await client.call_tool(
                "send_keys_batch",
                {
                    "operations": [
                        {"keys": "C-c", "enter": False},
                        {
                            "keys": "TUI_BATCH_DEFAULT",
                            "enter": True,
                            "literal": True,
                        },
                        {
                            "keys": "batch-secret",
                            "enter": True,
                            "literal": True,
                            "suppress_history": True,
                        },
                    ],
                    "timeout": 5.0,
                },
                raise_on_error=False,
            )
            assert result.is_error is False

    asyncio.run(_exercise())

    assert calls == [
        ["tmux", "send-keys", "-t", "%1", "C-c"],
        ["tmux", "send-keys", "-t", "%1", "-l", "TUI_BATCH_DEFAULT"],
        ["tmux", "send-keys", "-t", "%1", "Enter"],
        ["tmux", "send-keys", "-t", "%1", "-l", " batch-secret"],
        ["tmux", "send-keys", "-t", "%1", "Enter"],
    ]


def test_global_history_default_leaves_paste_payloads_and_calls_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paste tools preserve exact text and their existing buffer semantics.

    Subprocess and fake-pane interception observe the staged payload and paste
    call at the pre-PTY boundary, before tmux can obscure an inherited prefix.
    """
    from libtmux_mcp.tools import buffer_tools
    from libtmux_mcp.tools.pane_tools import io

    loaded_text: list[str] = []
    paste_calls: list[tuple[str, bool, bool]] = []

    class FakeServer:
        tmux_bin = "tmux"
        socket_name = None
        socket_path = None

        def delete_buffer(self, *, buffer_name: str) -> None:
            return None

    class FakePane:
        pane_id = "%1"

        def paste_buffer(
            self,
            *,
            buffer_name: str,
            bracket: bool,
            delete_after: bool = False,
        ) -> None:
            paste_calls.append((buffer_name, bracket, delete_after))

    def _run(argv: list[str], **kwargs: t.Any) -> subprocess.CompletedProcess[str]:
        if "load-buffer" in argv:
            loaded_text.append(pathlib.Path(argv[-1]).read_text())
        return subprocess.CompletedProcess(argv, 0)

    server = FakeServer()
    pane = FakePane()
    monkeypatch.setattr(io, "_get_server", lambda **kwargs: server)
    monkeypatch.setattr(io, "_resolve_pane", lambda *args, **kwargs: pane)
    monkeypatch.setattr("libtmux_mcp.tools.pane_tools.io.subprocess.run", _run)
    monkeypatch.setattr(buffer_tools, "_get_server", lambda **kwargs: server)
    monkeypatch.setattr(buffer_tools, "_resolve_pane", lambda *args, **kwargs: pane)

    raw_text = "C-c\npartial TUI text\n"
    existing_buffer = f"libtmux_mcp_{'a' * 32}_buf"

    async def _exercise() -> None:
        async with Client(_history_server("1")) as client:
            pasted_text = await client.call_tool(
                "paste_text",
                {"text": raw_text, "bracket": False},
                raise_on_error=False,
            )
            assert pasted_text.is_error is False
            pasted_buffer = await client.call_tool(
                "paste_buffer",
                {"buffer_name": existing_buffer, "bracket": True},
                raise_on_error=False,
            )
            assert pasted_buffer.is_error is False

    asyncio.run(_exercise())

    assert loaded_text == [raw_text]
    assert len(paste_calls) == 2
    assert paste_calls[0][1:] == (False, True)
    assert paste_calls[1] == (existing_buffer, True, False)


@pytest.mark.parametrize(
    "line_break",
    [pytest.param("\n", id="line-feed"), pytest.param("\r", id="carriage-return")],
)
def test_mcp_run_command_rejects_multiline_suppression_before_tmux(
    monkeypatch: pytest.MonkeyPatch,
    line_break: str,
) -> None:
    """The enabled omitted default rejects breakouts before tmux resolution."""
    from libtmux_mcp.tools.pane_tools import io

    private_marker = "PRIVATE_MULTILINE_MARKER"
    command = f"){line_break}printf '%s' {private_marker}{line_break}("

    def _unexpected_get_server(**_kwargs: t.Any) -> t.NoReturn:
        msg = "multiline validation must precede _get_server"
        raise AssertionError(msg)

    monkeypatch.setattr(io, "_get_server", _unexpected_get_server)

    async def _exercise() -> t.Any:
        async with Client(_history_server("1")) as client:
            return await client.call_tool(
                "run_command",
                {"command": command},
                raise_on_error=False,
            )

    result = asyncio.run(_exercise())

    assert result.is_error is True
    assert result.content
    assert result.content[0].text == (
        "command must be a single line when suppress_history=True"
    )
    assert private_marker not in repr(result)


def test_mcp_run_command_preserves_multiline_when_suppression_disabled(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """An explicit false keeps the existing multiline command behavior."""
    first = "MULTILINE_CONTROL_FIRST"
    second = "MULTILINE_CONTROL_SECOND"
    output = tmp_path / "multiline-control.txt"
    command = (
        f"printf '%s\\n' {first} > {shlex.quote(str(output))}\n"
        f"printf '%s\\n' {second} >> {shlex.quote(str(output))}"
    )

    async def _exercise() -> None:
        async with Client(_history_server("1")) as client:
            result = await client.call_tool(
                "run_command",
                {
                    "command": command,
                    "pane_id": mcp_pane.pane_id,
                    "timeout": 3.0,
                    "socket_name": mcp_server.socket_name,
                    "suppress_history": False,
                },
                raise_on_error=False,
            )
            _assert_run_command_succeeded(result)

    asyncio.run(_exercise())

    assert output.read_text().splitlines() == [first, second]
