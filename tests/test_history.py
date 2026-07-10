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
    SuppressHistorySettingFixture("unset", None, False),
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


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled"])
def test_history_transform_publishes_non_nullable_run_command_default(
    enabled: bool,
) -> None:
    """The MCP schema publishes the resolved semantic-command default."""
    from libtmux_mcp._history import _configure_history_defaults
    from libtmux_mcp.tools import pane_tools

    mcp = FastMCP("history-schema-probe")
    pane_tools.register(mcp)
    _configure_history_defaults(mcp, enabled)

    async def _list_tools() -> dict[str, t.Any]:
        async with Client(mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = asyncio.run(_list_tools())
    schema = tools["run_command"].inputSchema["properties"]["suppress_history"]

    assert schema["type"] == "boolean"
    assert schema["default"] is enabled
    assert "anyOf" not in schema


@pytest.mark.parametrize(
    SuppressHistorySettingFixture._fields,
    SUPPRESS_HISTORY_SETTING_FIXTURES,
    ids=[fixture.test_id for fixture in SUPPRESS_HISTORY_SETTING_FIXTURES],
)
def test_production_mcp_schema_uses_startup_history_default(
    test_id: str,
    value: str | None,
    expected: bool,
) -> None:
    """A fresh server publishes one stable transform with retained metadata."""
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
    assert payload["tags"] == ["mutating"]


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
        "space-prefixed history suppression."
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
def test_mcp_run_command_and_generic_batch_use_effective_default(
    test_id: str,
    value: str,
    omitted_is_saved: bool,
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """Omission inherits through direct and generic MCP calls; booleans win."""
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
            assert batch.structured_content["succeeded"] == 1
            [operation] = batch.structured_content["results"]
            assert operation["success"] is True
            nested = operation["structured_content"]
            assert nested is not None
            assert nested["timed_out"] is False
            assert nested["exit_status"] == 0
            assert batch_output.read_text() == f"{batch_omitted}\n"

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
    assert (batch_omitted in saved) is omitted_is_saved


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


def test_mcp_run_command_protects_complete_multiline_bash_history_event(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """One grouped multiline command executes fully without disk history."""
    histfile = tmp_path / "multiline.bash_history"
    mcp_pane.send_keys("exec bash --noprofile --norc", enter=True)
    retry_until(
        lambda: any("bash-" in line for line in mcp_pane.capture_pane()),
        2,
        raises=True,
    )
    setup = (
        f"HISTFILE={shlex.quote(str(histfile))}; "
        "HISTCONTROL=ignorespace; shopt -s cmdhist; shopt -u lithist; "
        "set -o history; history -c; history -w"
    )
    mcp_pane.send_keys(setup, enter=True)
    retry_until(histfile.exists, 2, raises=True)

    control_first = "MULTILINE_CONTROL_FIRST"
    control_second = "MULTILINE_CONTROL_SECOND"
    protected_first = "MULTILINE_PROTECTED_FIRST"
    protected_second = "MULTILINE_PROTECTED_SECOND"
    control_output = tmp_path / "multiline-control.txt"
    protected_output = tmp_path / "multiline-protected.txt"
    control_command = (
        f"printf '%s\\n' {control_first} > {shlex.quote(str(control_output))}\n"
        f"printf '%s\\n' {control_second} >> {shlex.quote(str(control_output))}"
    )
    protected_command = (
        f"printf '%s\\n' {protected_first} > {shlex.quote(str(protected_output))}\n"
        f"printf '%s\\n' {protected_second} >> {shlex.quote(str(protected_output))}"
    )
    base = {
        "pane_id": mcp_pane.pane_id,
        "timeout": 3.0,
        "socket_name": mcp_server.socket_name,
    }

    async def _exercise() -> None:
        async with Client(_history_server("1")) as client:
            control = await client.call_tool(
                "run_command",
                {
                    **base,
                    "command": control_command,
                    "suppress_history": False,
                },
                raise_on_error=False,
            )
            _assert_run_command_succeeded(control)
            protected = await client.call_tool(
                "run_command",
                {**base, "command": protected_command},
                raise_on_error=False,
            )
            _assert_run_command_succeeded(protected)
            flushed = await client.call_tool(
                "run_command",
                {
                    **base,
                    "command": "history -w",
                    "suppress_history": True,
                },
                raise_on_error=False,
            )
            _assert_run_command_succeeded(flushed)

    asyncio.run(_exercise())
    saved_lines = histfile.read_text().splitlines()
    control_entries = [
        line for line in saved_lines if control_first in line or control_second in line
    ]

    assert control_output.read_text().splitlines() == [control_first, control_second]
    assert protected_output.read_text().splitlines() == [
        protected_first,
        protected_second,
    ]
    assert len(control_entries) == 1
    assert control_first in control_entries[0]
    assert control_second in control_entries[0]
    assert all(protected_first not in line for line in saved_lines)
    assert all(protected_second not in line for line in saved_lines)
