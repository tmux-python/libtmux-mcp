"""Tests for history-safe tmux spawn tool behavior."""

from __future__ import annotations

import asyncio
import inspect
import logging
import shlex
import typing as t

import pytest
from fastmcp import Client, FastMCP
from libtmux.test.retry import retry_until

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session


def test_spawn_tool_signatures_preserve_positional_slots() -> None:
    """New spawn options are appended as keyword-only parameters."""
    from libtmux_mcp.tools.pane_tools import respawn_pane
    from libtmux_mcp.tools.server_tools import create_session
    from libtmux_mcp.tools.session_tools import create_window
    from libtmux_mcp.tools.window_tools import split_window

    expected: dict[
        t.Callable[..., t.Any],
        tuple[tuple[str, ...], tuple[str, ...]],
    ] = {
        create_session: (
            (
                "session_name",
                "window_name",
                "start_directory",
                "x",
                "y",
                "environment",
                "socket_name",
            ),
            ("suppress_history",),
        ),
        create_window: (
            (
                "session_name",
                "session_id",
                "window_name",
                "start_directory",
                "attach",
                "direction",
                "socket_name",
            ),
            ("environment", "suppress_history"),
        ),
        split_window: (
            (
                "pane_id",
                "session_name",
                "session_id",
                "window_id",
                "window_index",
                "direction",
                "size",
                "start_directory",
                "shell",
                "socket_name",
            ),
            ("environment", "suppress_history"),
        ),
        respawn_pane: (
            (
                "pane_id",
                "kill",
                "shell",
                "start_directory",
                "environment",
                "socket_name",
            ),
            ("suppress_history",),
        ),
    }

    for function, (positional_names, keyword_only_names) in expected.items():
        signature = inspect.signature(function)
        parameters = signature.parameters
        assert tuple(parameters) == positional_names + keyword_only_names
        assert all(
            parameters[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            for name in positional_names
        )
        assert all(
            parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
            for name in keyword_only_names
        )
        assert parameters["suppress_history"].default is False
        bound = signature.bind(*([None] * len(positional_names)))
        assert tuple(bound.arguments) == positional_names
        with pytest.raises(TypeError):
            signature.bind(*([None] * (len(positional_names) + 1)))


def test_spawn_environment_schemas_are_client_compatible() -> None:
    """Every spawn tool accepts object and JSON-string environment input."""
    from libtmux_mcp.tools import register_tools

    mcp = FastMCP("spawn-environment-schema")
    register_tools(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    for name in ("create_session", "create_window", "split_window", "respawn_pane"):
        environment = tools[name].parameters["properties"]["environment"]
        assert {variant["type"] for variant in environment["anyOf"]} == {
            "object",
            "string",
            "null",
        }
        suppress_history = tools[name].parameters["properties"]["suppress_history"]
        assert suppress_history["type"] == "boolean"
        assert suppress_history["default"] is False


def _assert_value_free_spawn_conflict(call: t.Callable[[], t.Any], name: str) -> None:
    """Assert one spawn call rejects a policy conflict without its value."""
    from fastmcp.exceptions import ToolError

    supplied = "private-conflicting-history-value"
    with pytest.raises(ToolError) as excinfo:
        call()

    assert str(excinfo.value) == (
        f"environment variable {name} conflicts with suppress_history=True; "
        "omit it or set it to an empty string"
    )
    assert supplied not in str(excinfo.value)


def test_spawn_validation_precedes_every_server_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected environment performs zero tmux lookups or retry attempts."""
    from libtmux_mcp.tools import server_tools, session_tools, window_tools
    from libtmux_mcp.tools.pane_tools import lifecycle

    calls: list[str] = []

    def _unexpected_get_server(**_kwargs: t.Any) -> t.NoReturn:
        calls.append("_get_server")
        message = "spawn validation must precede _get_server"
        raise AssertionError(message)

    for module in (server_tools, session_tools, window_tools, lifecycle):
        monkeypatch.setattr(module, "_get_server", _unexpected_get_server)

    environment = {"HISTFILE": "private-conflicting-history-value"}
    spawn_calls = (
        lambda: server_tools.create_session(
            environment=environment,
            suppress_history=True,
        ),
        lambda: session_tools.create_window(
            environment=environment,
            suppress_history=True,
        ),
        lambda: window_tools.split_window(
            environment=environment,
            suppress_history=True,
        ),
        lambda: lifecycle.respawn_pane(
            pane_id="%1",
            environment=environment,
            suppress_history=True,
        ),
    )

    for call in spawn_calls:
        _assert_value_free_spawn_conflict(call, "HISTFILE")

    assert calls == []


def test_spawn_conflicts_create_nothing_and_never_retry_unsuppressed(
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """All spawn tools reject before creating or replacing a tmux process."""
    from libtmux_mcp.tools.pane_tools import respawn_pane
    from libtmux_mcp.tools.server_tools import create_session
    from libtmux_mcp.tools.session_tools import create_window
    from libtmux_mcp.tools.window_tools import split_window

    supplied = "private-conflicting-history-value"
    environment = {"HISTFILE": supplied}

    session_ids = {session.session_id for session in mcp_server.sessions}
    _assert_value_free_spawn_conflict(
        lambda: create_session(
            session_name="history_conflict_session",
            environment=environment,
            socket_name=mcp_server.socket_name,
            suppress_history=True,
        ),
        "HISTFILE",
    )
    assert {session.session_id for session in mcp_server.sessions} == session_ids

    window_ids = {window.window_id for window in mcp_session.windows}
    _assert_value_free_spawn_conflict(
        lambda: create_window(
            session_name=mcp_session.session_name,
            window_name="history_conflict_window",
            socket_name=mcp_server.socket_name,
            environment=environment,
            suppress_history=True,
        ),
        "HISTFILE",
    )
    assert {window.window_id for window in mcp_session.windows} == window_ids

    window = mcp_session.active_window
    pane_ids = {pane.pane_id for pane in window.panes}
    _assert_value_free_spawn_conflict(
        lambda: split_window(
            window_id=window.window_id,
            socket_name=mcp_server.socket_name,
            environment=environment,
            suppress_history=True,
        ),
        "HISTFILE",
    )
    assert {pane.pane_id for pane in window.panes} == pane_ids

    pane = window.split(shell="sleep 30")
    assert pane.pane_id is not None
    pane.refresh()
    original_pid = pane.pane_pid
    try:
        _assert_value_free_spawn_conflict(
            lambda: respawn_pane(
                pane_id=t.cast("str", pane.pane_id),
                environment=environment,
                socket_name=mcp_server.socket_name,
                suppress_history=True,
            ),
            "HISTFILE",
        )
        pane.refresh()
        assert pane.pane_pid == original_pid
    finally:
        pane.kill()


def test_mcp_spawn_explicit_false_overrides_enabled_default(
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """An explicit false override permits a caller history environment."""
    from libtmux_mcp._history import _configure_history_defaults
    from libtmux_mcp.tools import session_tools

    mcp = FastMCP("spawn-override-probe")
    session_tools.register(mcp)
    _configure_history_defaults(mcp, True)
    supplied = "private-explicit-history-path"
    base = {
        "session_name": mcp_session.session_name,
        "environment": {"HISTFILE": supplied},
        "socket_name": mcp_server.socket_name,
    }
    before = {window.window_id for window in mcp_session.windows}

    async def _exercise() -> None:
        async with Client(mcp) as client:
            inherited = await client.call_tool(
                "create_window",
                {**base, "window_name": "spawn_default_conflict"},
                raise_on_error=False,
            )
            assert inherited.is_error is True
            assert inherited.content
            error = inherited.content[0].text
            assert error == (
                "environment variable HISTFILE conflicts with "
                "suppress_history=True; omit it or set it to an empty string"
            )
            assert supplied not in error
            assert {window.window_id for window in mcp_session.windows} == before

            overridden = await client.call_tool(
                "create_window",
                {
                    **base,
                    "window_name": "spawn_explicit_false",
                    "suppress_history": False,
                },
                raise_on_error=False,
            )
            assert overridden.is_error is False
            assert overridden.structured_content is not None
            assert overridden.structured_content["window_name"] == (
                "spawn_explicit_false"
            )

    asyncio.run(_exercise())
    created = [
        window for window in mcp_session.windows if window.window_id not in before
    ]
    assert len(created) == 1
    assert created[0].window_name == "spawn_explicit_false"


def _spawn_history_server(enabled: bool) -> FastMCP:
    """Build a complete server for spawn transforms and generic batches."""
    from libtmux_mcp._history import _configure_history_defaults
    from libtmux_mcp.tools import register_tools

    mcp = FastMCP(f"spawn-history-{enabled}")
    register_tools(mcp)
    _configure_history_defaults(mcp, enabled)
    return mcp


def test_generic_batch_spawn_uses_default_and_explicit_false_override(
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """Nested spawn calls retain transform defaults, errors, and inner state."""
    supplied = "private-nested-history-path"
    before = {window.window_id for window in mcp_session.windows}
    base = {
        "session_name": mcp_session.session_name,
        "environment": {"HISTFILE": supplied},
        "socket_name": mcp_server.socket_name,
    }

    async def _exercise() -> t.Any:
        async with Client(_spawn_history_server(True)) as client:
            return await client.call_tool(
                "call_mutating_tools_batch",
                {
                    "on_error": "continue",
                    "operations": [
                        {
                            "tool": "create_window",
                            "arguments": {
                                **base,
                                "window_name": "batch_spawn_default_conflict",
                            },
                        },
                        {
                            "tool": "create_window",
                            "arguments": {
                                **base,
                                "window_name": "batch_spawn_explicit_false",
                                "suppress_history": False,
                            },
                        },
                    ],
                },
                raise_on_error=False,
            )

    result = asyncio.run(_exercise())

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["succeeded"] == 1
    assert result.structured_content["failed"] == 1
    assert result.structured_content["stopped_at"] is None
    failed, succeeded = result.structured_content["results"]
    assert failed["success"] is False
    assert failed["error"] == (
        "environment variable HISTFILE conflicts with suppress_history=True; "
        "omit it or set it to an empty string"
    )
    assert supplied not in failed["error"]
    assert succeeded["success"] is True
    assert succeeded["structured_content"]["window_name"] == (
        "batch_spawn_explicit_false"
    )
    created = {window.window_id for window in mcp_session.windows} - before
    assert len(created) == 1


def test_spawn_conflict_is_absent_from_tool_results_and_logs(
    mcp_server: Server,
    mcp_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Validation and audit surfaces never reproduce a conflicting value."""
    from libtmux_mcp.server import build_mcp_server

    supplied = "private-validation-log-sentinel"

    async def _exercise() -> t.Any:
        async with Client(build_mcp_server()) as client:
            return await client.call_tool(
                "create_window",
                {
                    "session_name": mcp_session.session_name,
                    "window_name": "spawn_log_conflict",
                    "environment": {"HISTFILE": supplied},
                    "socket_name": mcp_server.socket_name,
                    "suppress_history": True,
                },
                raise_on_error=False,
            )

    with caplog.at_level(logging.DEBUG):
        result = asyncio.run(_exercise())

    assert result.is_error is True
    assert result.content
    assert result.content[0].text == (
        "environment variable HISTFILE conflicts with suppress_history=True; "
        "omit it or set it to an empty string"
    )
    assert supplied not in repr(result)
    assert supplied not in "\n".join(record.getMessage() for record in caplog.records)
    assert supplied not in repr([record.__dict__ for record in caplog.records])
    audit = [record for record in caplog.records if record.name == "libtmux_mcp.audit"]
    assert audit
    assert any(
        "tool=create_window outcome=error" in record.getMessage() for record in audit
    )


def _assert_pane_environment(
    pane: Pane,
    *,
    marker: str,
    expected: str,
) -> None:
    """Read an expanded environment tuple from a live spawned process."""
    pane.send_keys(
        "printf '" + marker + ":<%s>|%s|%s|<%s>|%s\\n' "
        '"$HISTFILE" "$HISTCONTROL" "$fish_private_mode" "$fish_history" '
        '"$SPAWN_SCOPE_MARKER"',
        enter=True,
    )
    retry_until(
        lambda: any(
            expected in line
            for line in pane.cmd("capture-pane", "-p", "-S", "-50").stdout
        ),
        3,
        raises=True,
    )


def _session_environment(server: Server, session_name: str) -> list[str]:
    """Return the complete tmux session environment."""
    return server.cmd("show-environment", "-t", session_name).stdout


def test_spawn_tools_forward_process_environment_without_session_leakage(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """Window, split, and respawn environments remain process-scoped."""
    from libtmux_mcp.tools.pane_tools import respawn_pane
    from libtmux_mcp.tools.session_tools import create_window
    from libtmux_mcp.tools.window_tools import split_window

    session_name = mcp_pane.session.session_name
    assert session_name is not None
    before = _session_environment(mcp_server, session_name)
    assert all(not line.startswith("SPAWN_SCOPE_MARKER=") for line in before)

    window_info = create_window(
        session_name=session_name,
        window_name="history_process_window",
        socket_name=mcp_server.socket_name,
        environment='{"SPAWN_SCOPE_MARKER":"window"}',
        suppress_history=True,
    )
    assert window_info.active_pane_id is not None
    window_pane = mcp_server.panes.get(
        pane_id=window_info.active_pane_id,
        default=None,
    )
    assert window_pane is not None
    window_pane.send_keys(
        "printf 'WINDOW_PROCESS:%s\\n' \"$SPAWN_SCOPE_MARKER\"",
        enter=True,
    )
    retry_until(
        lambda: any(
            "WINDOW_PROCESS:window" in line for line in window_pane.capture_pane()
        ),
        3,
        raises=True,
    )
    assert all(
        not line.startswith("SPAWN_SCOPE_MARKER=")
        for line in _session_environment(mcp_server, session_name)
    )

    pane_info = split_window(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
        environment={
            "SPAWN_SCOPE_MARKER": "split",
            "HISTCONTROL": "ignoredups",
        },
        suppress_history=True,
    )
    assert pane_info.pane_id is not None
    split_pane = mcp_server.panes.get(pane_id=pane_info.pane_id, default=None)
    assert split_pane is not None
    _assert_pane_environment(
        split_pane,
        marker="SPLIT_PROCESS",
        expected="SPLIT_PROCESS:<>|ignoredups:ignorespace|1|<>|split",
    )
    assert all(
        not line.startswith("SPAWN_SCOPE_MARKER=")
        for line in _session_environment(mcp_server, session_name)
    )

    window_branch_info = split_window(
        window_id=mcp_pane.window.window_id,
        socket_name=mcp_server.socket_name,
        environment={
            "SPAWN_SCOPE_MARKER": "window-branch",
            "HISTCONTROL": "ignoredups",
        },
        suppress_history=True,
    )
    assert window_branch_info.pane_id is not None
    window_branch_pane = mcp_server.panes.get(
        pane_id=window_branch_info.pane_id,
        default=None,
    )
    assert window_branch_pane is not None
    _assert_pane_environment(
        window_branch_pane,
        marker="WINDOW_BRANCH_PROCESS",
        expected=("WINDOW_BRANCH_PROCESS:<>|ignoredups:ignorespace|1|<>|window-branch"),
    )
    assert all(
        not line.startswith("SPAWN_SCOPE_MARKER=")
        for line in _session_environment(mcp_server, session_name)
    )

    later_pane_info = split_window(
        window_id=mcp_pane.window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert later_pane_info.pane_id is not None
    later_pane = mcp_server.panes.get(
        pane_id=later_pane_info.pane_id,
        default=None,
    )
    assert later_pane is not None
    later_pane.send_keys(
        "printf 'LATER_PROCESS:<%s>\\n' \"$SPAWN_SCOPE_MARKER\"",
        enter=True,
    )
    retry_until(
        lambda: any(
            "LATER_PROCESS:<>" in line
            for line in later_pane.cmd("capture-pane", "-p", "-S", "-50").stdout
        ),
        3,
        raises=True,
    )

    respawn_target = later_pane
    assert respawn_target.pane_id is not None
    try:
        respawn_pane(
            pane_id=respawn_target.pane_id,
            shell="sh",
            environment=('{"SPAWN_SCOPE_MARKER":"respawn","HISTCONTROL":"ignoredups"}'),
            socket_name=mcp_server.socket_name,
            suppress_history=True,
        )
        _assert_pane_environment(
            respawn_target,
            marker="RESPAWN_PROCESS",
            expected="RESPAWN_PROCESS:<>|ignoredups:ignorespace|1|<>|respawn",
        )
        assert all(
            not line.startswith("SPAWN_SCOPE_MARKER=")
            for line in _session_environment(mcp_server, session_name)
        )
        respawn_pane(
            pane_id=respawn_target.pane_id,
            shell="sh",
            socket_name=mcp_server.socket_name,
        )
        respawn_target.send_keys(
            "printf 'LATER_RESPAWN:<%s>\\n' \"$SPAWN_SCOPE_MARKER\"",
            enter=True,
        )
        retry_until(
            lambda: any(
                "LATER_RESPAWN:<>" in line
                for line in respawn_target.cmd(
                    "capture-pane",
                    "-p",
                    "-S",
                    "-50",
                ).stdout
            ),
            3,
            raises=True,
        )
    finally:
        respawn_target.kill()


def test_create_session_history_environment_reaches_future_panes(
    mcp_server: Server,
) -> None:
    """Create-session stores policy values for the session and later panes."""
    from libtmux_mcp.tools.server_tools import create_session

    docstring = inspect.getdoc(create_session) or ""
    assert "session environment" in docstring
    assert "future panes" in docstring

    session_name = "history_session_scope"
    create_session(
        session_name=session_name,
        environment={"SPAWN_SCOPE_MARKER": "session", "HISTCONTROL": "ignoredups"},
        socket_name=mcp_server.socket_name,
        suppress_history=True,
    )
    session = mcp_server.sessions.get(session_name=session_name, default=None)
    assert session is not None
    try:
        rendered = _session_environment(mcp_server, session_name)
        assert "SPAWN_SCOPE_MARKER=session" in rendered
        assert "HISTFILE=" in rendered
        assert "HISTCONTROL=ignoredups:ignorespace" in rendered
        assert "fish_private_mode=1" in rendered
        assert "fish_history=" in rendered

        shell = (
            'sh -c \'printf "SESSION_FUTURE:<%s>|%s|%s|<%s>|%s\\\\n" '
            '"$HISTFILE" "$HISTCONTROL" "$fish_private_mode" "$fish_history" '
            '"$SPAWN_SCOPE_MARKER"; sleep 30\''
        )
        future_window = session.new_window(window_shell=shell)
        future_pane = future_window.active_pane
        assert future_pane is not None
        retry_until(
            lambda: any(
                "SESSION_FUTURE:<>|ignoredups:ignorespace|1|<>|session" in line
                for line in future_pane.capture_pane()
            ),
            3,
            raises=True,
        )
    finally:
        session.kill()


def test_explicit_false_keeps_inherited_session_history_environment(
    mcp_server: Server,
) -> None:
    """A process override does not erase policy already stored on its session."""
    from libtmux_mcp.tools.server_tools import create_session
    from libtmux_mcp.tools.session_tools import create_window

    session_name = "history_explicit_false_inheritance"
    create_session(
        session_name=session_name,
        environment={"HISTCONTROL": "ignoredups"},
        socket_name=mcp_server.socket_name,
        suppress_history=True,
    )
    session = mcp_server.sessions.get(session_name=session_name, default=None)
    assert session is not None
    try:
        shell = (
            'sh -c \'printf "EXPLICIT_FALSE_INHERITED:<%s>|%s|%s|<%s>|%s\\\\n" '
            '"$HISTFILE" "$HISTCONTROL" "$fish_private_mode" "$fish_history" '
            '"$EXPLICIT_FALSE_MARKER"; sleep 30\''
        )
        mcp_server.cmd(
            "set-option",
            "-t",
            session_name,
            "default-command",
            shell,
        )
        window_info = create_window(
            session_name=session_name,
            window_name="explicit_false_inherited",
            socket_name=mcp_server.socket_name,
            environment={"EXPLICIT_FALSE_MARKER": "process"},
            suppress_history=False,
        )
        assert window_info.active_pane_id is not None
        pane = mcp_server.panes.get(
            pane_id=window_info.active_pane_id,
            default=None,
        )
        assert pane is not None
        retry_until(
            lambda: any(
                "EXPLICIT_FALSE_INHERITED:<>|ignoredups:ignorespace|1|<>|process"
                in line
                for line in pane.capture_pane()
            ),
            3,
            raises=True,
        )
        rendered = _session_environment(mcp_server, session_name)
        assert "HISTFILE=" in rendered
        assert "HISTCONTROL=ignoredups:ignorespace" in rendered
        assert "EXPLICIT_FALSE_MARKER=process" not in rendered
    finally:
        session.kill()


def test_spawn_tools_preserve_direct_launch_strings(
    mcp_server: Server,
    mcp_pane: Pane,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """History environment flags never rewrite direct tmux launch strings."""
    from libtmux_mcp.tools.pane_tools import respawn_pane
    from libtmux_mcp.tools.window_tools import split_window

    pane_branch_shell = "sh -c 'sleep 30'"
    window_branch_shell = "sh -c 'sleep 31'"
    with caplog.at_level(logging.DEBUG, logger="libtmux.common"):
        pane_branch = split_window(
            pane_id=mcp_pane.pane_id,
            shell=pane_branch_shell,
            socket_name=mcp_server.socket_name,
            suppress_history=True,
        )
        window_branch = split_window(
            window_id=mcp_pane.window.window_id,
            shell=window_branch_shell,
            socket_name=mcp_server.socket_name,
            suppress_history=True,
        )
    split_commands = [
        shlex.split(t.cast("str", record.__dict__["tmux_cmd"]))
        for record in caplog.records
        if " split-window " in record.__dict__.get("tmux_cmd", "")
        and record.getMessage() == "tmux command dispatched"
    ]
    assert [command[-1] for command in split_commands] == [
        pane_branch_shell,
        window_branch_shell,
    ]

    assert pane_branch.pane_id is not None
    assert window_branch.pane_id is not None
    respawn_shell = "sh -c 'sleep 29'"
    caplog.clear()
    try:
        with caplog.at_level(logging.DEBUG, logger="libtmux.common"):
            respawn_pane(
                pane_id=pane_branch.pane_id,
                shell=respawn_shell,
                socket_name=mcp_server.socket_name,
                suppress_history=True,
            )
        respawn_commands = [
            shlex.split(t.cast("str", record.__dict__["tmux_cmd"]))
            for record in caplog.records
            if " respawn-pane " in record.__dict__.get("tmux_cmd", "")
            and record.getMessage() == "tmux command dispatched"
        ]
        assert [command[-1] for command in respawn_commands] == [respawn_shell]
    finally:
        for pane_id in (pane_branch.pane_id, window_branch.pane_id):
            pane = mcp_server.panes.get(pane_id=pane_id, default=None)
            if pane is not None:
                pane.kill()
