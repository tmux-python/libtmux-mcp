"""Owned program tools through the public MCP client."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import pathlib
import sys
import threading
import typing as t

import anyio
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.dependencies import get_context
from libtmux import Session, program
from libtmux.common import has_gte_version
from mcp.types import CancelledNotification, CancelledNotificationParams

from libtmux_mcp import _programs
from libtmux_mcp.middleware import ToolErrorResultMiddleware
from libtmux_mcp.server import build_mcp_server
from libtmux_mcp.tools import program_tools, register_tools


def _application() -> FastMCP:
    app = FastMCP("owned-program-test")
    app.add_middleware(ToolErrorResultMiddleware())
    register_tools(app)
    return app


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
def test_program_run_returns_exit_output_and_preserves_parent(
    mcp_session: Session, tmp_path: pathlib.Path
) -> None:
    """One public call owns argv, environment, geometry and terminal cleanup."""
    before = mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout
    arguments = ["space value", "$HOME", "semi;", ""]
    source = (
        "import json,os,sys; "
        "print(json.dumps([sys.argv[1:], os.getcwd(), os.environ['PROGRAM_VALUE'], "
        "list(os.get_terminal_size())])); print('last line'); raise SystemExit(7)"
    )

    async def exercise() -> None:
        async with Client(_application()) as client:
            listed = {tool.name for tool in await client.list_tools()}
            assert {"run_program", "list_program_runs", "cleanup_program"} <= listed
            result = await client.call_tool(
                "run_program",
                {
                    "session_id": mcp_session.session_id,
                    "socket_name": mcp_session.server.socket_name,
                    "argv": [sys.executable, "-c", source, *arguments],
                    "start_directory": str(tmp_path),
                    "environment": {"PROGRAM_VALUE": "literal ; $HOME"},
                    "width": 90,
                    "height": 21,
                },
            )
            data = result.structured_content
            assert data is not None
            assert data["exit_status"] == 7 and data["exit_signal"] is None
            expected = json.dumps(
                [arguments, str(tmp_path), "literal ; $HOME", [90, 21]]
            )
            assert expected in data["screen_text"]
            assert data["screen_text"].endswith("last line")
            assert data["pane_removed"] and not data["screen_truncated"]
            assert data["history_complete"] is None
            records = await client.call_tool("list_program_runs", {})
            assert records.data.items == []

    asyncio.run(exercise())
    assert mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout == before


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
@pytest.mark.parametrize("malformed", [False, True])
def test_program_audit_redacts_argv_before_validation(
    mcp_session: Session, caplog: pytest.LogCaptureFixture, malformed: bool
) -> None:
    """Public calls redact both accepted argv and rejected payload shapes."""
    secret = "PROGRAM_ARG_PAYLOAD_72be"
    environment_secret = "PROGRAM_ENV_PAYLOAD_d628"
    argv: t.Any = (
        {secret: "invalid list"}
        if malformed
        else [sys.executable, "-c", "raise SystemExit(7)", secret]
    )

    async def exercise() -> None:
        async with Client(build_mcp_server()) as client:
            result = await client.call_tool(
                "run_program",
                {
                    "session_id": mcp_session.session_id,
                    "socket_name": mcp_session.server.socket_name,
                    "argv": argv,
                    "environment": {"PROGRAM_VALUE": environment_secret},
                },
                raise_on_error=False,
            )
            assert result.is_error is malformed
            if not malformed:
                assert result.structured_content is not None
                assert result.structured_content["exit_status"] == 7

    with caplog.at_level(logging.INFO, logger="libtmux_mcp.audit"):
        asyncio.run(exercise())
    messages = [record.getMessage() for record in caplog.records]
    audit = [
        record.getMessage()
        for record in caplog.records
        if record.name == "libtmux_mcp.audit"
    ]
    assert any("tool=run_program" in message for message in audit)
    assert any("sha256_prefix" in message for message in audit)
    assert all(secret not in message for message in messages)
    assert all(environment_secret not in message for message in messages)


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
def test_program_rejected_launch_recovers_after_verified_removal(
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Native errors hide payloads and absent-pane cleanup survives lost observation."""
    store = _programs._ProgramRuns()
    monkeypatch.setattr(program_tools, "_program_runs", store)
    native = program._Client.__init__
    argument = "PROGRAM_RECOVERY_ARG_ba71"
    environment = "PROGRAM_RECOVERY_ENV_c329"
    before = mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout

    def reject_launch(
        client: program._Client, command: tuple[str, ...], *args: t.Any
    ) -> None:
        changed = tuple(
            value.replace("respawn-pane ", "program-test-invalid-command ", 1)
            for value in command
        )
        native(client, changed, *args)

    def retain_owner(*args: t.Any, **kwargs: t.Any) -> None:
        msg = "Controlled initial removal failure"
        raise OSError(msg)

    async def exercise() -> None:
        async with Client(build_mcp_server()) as client:
            run_id = None
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(program._Client, "__init__", reject_launch)
                    patch.setattr(program.Program, "_remove", retain_owner)
                    result = await client.call_tool(
                        "run_program",
                        {
                            "session_id": mcp_session.session_id,
                            "socket_name": mcp_session.server.socket_name,
                            "argv": [
                                sys.executable,
                                "-c",
                                "raise SystemExit(7)",
                                argument,
                            ],
                            "environment": {"PROGRAM_VALUE": environment},
                        },
                        raise_on_error=False,
                    )
                assert result.is_error
                run_id = (result.meta or {})["program_recovery_id"]
                detail = await client.call_tool("get_program_run", {"run_id": run_id})
                page = await client.call_tool("list_program_runs", {})
                evidence = repr((result, detail, page))
                assert argument not in evidence and environment not in evidence
                records = repr([record.__dict__ for record in caplog.records])
                assert argument not in records and environment not in records
                refused = await client.call_tool(
                    "cleanup_program", {"run_id": run_id}, raise_on_error=False
                )
                assert refused.is_error
                assert (refused.meta or {})["program_recovery_id"] == run_id
                cleanup_errors = (refused.meta or {})["program_cleanup_errors"]
                assert "submitted program lifetime is unknown" in cleanup_errors[-2]
                assert (
                    "program lifetime observation is unavailable" in cleanup_errors[-1]
                )
                owner = store._runs[run_id].owner
                assert owner is not None and owner.recovery.cleanup_pending
                pane_id = owner.recovery.pane_id
                assert pane_id is not None
                assert (
                    pane_id
                    in mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout
                )
                mcp_session.cmd("kill-pane", "-t", pane_id)
                cleaned = await client.call_tool(
                    "cleanup_program", {"run_id": run_id}, raise_on_error=False
                )
                assert not cleaned.is_error
                assert cleaned.structured_content is not None
                assert not cleaned.structured_content["cleanup_pending"]
                assert (
                    await client.call_tool("list_program_runs", {})
                ).data.items == []
            finally:
                if run_id is not None and run_id in store._runs:
                    retained = store._runs[run_id]
                    assert retained.owner is not None
                    owner = retained.owner
                    panes = mcp_session.cmd(
                        "list-panes", "-s", "-F", "#{pane_id}"
                    ).stdout
                    pane_id = owner.recovery.pane_id
                    if pane_id is not None and pane_id in panes:
                        mcp_session.cmd("kill-pane", "-t", pane_id)
                    owner.cleanup(timeout=0.5)
                    assert not owner.recovery.cleanup_pending
                    store.retire(retained)

    with caplog.at_level(logging.DEBUG, logger="libtmux.program"):
        asyncio.run(exercise())
    assert mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout == before


def test_program_capabilities_report_the_connected_daemon(mcp_session: Session) -> None:
    """Discovery serializes core decisions without a second version table."""
    expected = mcp_session.server.capabilities()

    async def exercise() -> None:
        async with Client(_application()) as client:
            result = await client.call_tool(
                "get_server_capabilities",
                {"socket_name": mcp_session.server.socket_name},
            )
            data = result.structured_content
            assert data is not None
            assert data["pid"] == expected.pid
            assert data["start_time"] == expected.start_time
            assert data["version"] == expected.version
            assert (
                data["features"][0]["status"]
                == expected.feature("program-owned").status
            )
            assert (
                data["features"][0]["reason"]
                == expected.feature("program-owned").reason
            )

    asyncio.run(exercise())


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
def test_program_tail_reports_byte_truncation(mcp_session: Session) -> None:
    """The public result keeps a valid UTF-8 tail and explicitly reports loss."""

    async def exercise() -> None:
        async with Client(_application()) as client:
            result = await client.call_tool(
                "run_program",
                {
                    "session_id": mcp_session.session_id,
                    "socket_name": mcp_session.server.socket_name,
                    "argv": [
                        sys.executable,
                        "-c",
                        "print('prefix' + 'é' * 20 + 'END')",
                    ],
                    "output_bytes": 10,
                },
            )
            data = result.structured_content
            assert data is not None
            assert data["screen_truncated"]
            assert data["screen_text"] == "éééEND"
            assert len(data["screen_text"].encode()) <= 10

    asyncio.run(exercise())


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
def test_program_cleanup_retry_keeps_exit_and_capture(
    mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inject a real buffer cleanup failure and recover through the MCP owner ID."""
    discard = program.Program._discard_capture

    def fail_discard(owner: program.Program) -> None:
        if owner._capture_buffer is not None:
            msg = "Capture buffer cleanup is temporarily unavailable"
            raise OSError(msg)
        discard(owner)

    async def exercise() -> None:
        async with Client(_application()) as client:
            run_id = None
            cleanup_task = None
            admitted, release = asyncio.Event(), asyncio.Event()
            worker = _programs._run_in_worker

            async def queued(call: t.Callable[[threading.Event], t.Any]) -> t.Any:
                admitted.set()
                await release.wait()
                return await worker(call)

            try:
                with monkeypatch.context() as patch:
                    patch.setattr(program.Program, "_discard_capture", fail_discard)
                    result = await client.call_tool(
                        "run_program",
                        {
                            "session_id": mcp_session.session_id,
                            "socket_name": mcp_session.server.socket_name,
                            "argv": [
                                sys.executable,
                                "-c",
                                "print('kept'); raise SystemExit(7)",
                            ],
                        },
                        raise_on_error=False,
                    )
                assert result.is_error
                meta = result.meta or {}
                run_id = meta["program_recovery_id"]
                assert meta["program_cleanup_errors"]
                assert meta["program_observation"]["exit_status"] == 7
                assert meta["program_observation"]["screen_text"] == "kept"
                records = await client.call_tool("list_program_runs", {})
                assert len(records.data.items) == 1
                record = records.data.items[0]
                assert record.run_id == run_id
                assert record.pane_state == "removed"
                assert record.cleanup_pending
                detail = await client.call_tool("get_program_run", {"run_id": run_id})
                assert detail.structured_content is not None
                assert detail.structured_content["last_observation"]["exit_status"] == 7
                # Hold worker admission to expose duplicate queued cleanup requests.
                with monkeypatch.context() as patch:
                    patch.setattr(_programs, "_run_in_worker", queued)
                    cleanup_task = asyncio.create_task(
                        client.call_tool("cleanup_program", {"run_id": run_id})
                    )
                    await asyncio.wait_for(admitted.wait(), 1)
                    records = await client.call_tool("list_program_runs", {})
                    assert records.data.items[0].status == "cleanup_in_progress"
                    duplicate = await asyncio.wait_for(
                        client.call_tool(
                            "cleanup_program", {"run_id": run_id}, raise_on_error=False
                        ),
                        1,
                    )
                    assert duplicate.is_error
            finally:
                release.set()
                if cleanup_task is not None:
                    result = await cleanup_task
                    assert result.structured_content is not None
                    assert result.structured_content["cleanup_pending"] is False
                elif run_id is not None:
                    result = await client.call_tool(
                        "cleanup_program", {"run_id": run_id}
                    )
                    assert result.structured_content is not None
                    assert result.structured_content["cleanup_pending"] is False
            assert (await client.call_tool("list_program_runs", {})).data.items == []

    asyncio.run(exercise())


def test_program_rejected_parent_does_not_consume_capacity(
    mcp_session: Session,
) -> None:
    """An effect-free refusal leaves the bounded recovery table empty."""

    async def exercise() -> None:
        async with Client(_application()) as client:
            result = await client.call_tool(
                "run_program",
                {
                    "session_id": "$999999999",
                    "socket_name": mcp_session.server.socket_name,
                    "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                },
                raise_on_error=False,
            )
            assert result.is_error
            assert "program_recovery_id" not in (result.meta or {})
            assert (await client.call_tool("list_program_runs", {})).data.items == []

    asyncio.run(exercise())


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
def test_program_recovery_pages_survive_deleted_cursor(
    mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain real failed buffers; deletion between pages cannot repeat an owner."""
    discard = program.Program._discard_capture

    def fail_discard(owner: program.Program) -> None:
        if owner._capture_buffer is not None:
            raise OSError("Capture cleanup unavailable: " + "é" * 4096)
        discard(owner)

    async def exercise() -> None:
        async with Client(_application()) as client:
            retained: list[str] = []
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(program.Program, "_discard_capture", fail_discard)
                    for _ in range(3):
                        result = await client.call_tool(
                            "run_program",
                            {
                                "session_id": mcp_session.session_id,
                                "socket_name": mcp_session.server.socket_name,
                                "argv": [sys.executable, "-c", "print('retained')"],
                            },
                            raise_on_error=False,
                        )
                        retained.append((result.meta or {})["program_recovery_id"])
                first = (
                    await client.call_tool("list_program_runs", {"limit": 2})
                ).structured_content
                assert first is not None
                assert [item["run_id"] for item in first["items"]] == retained[:2]
                assert first["next_cursor"] is not None
                for item in first["items"]:
                    assert len(item["error_preview"].encode()) <= 256
                    assert "cleanup_errors" not in item
                    assert "last_observation" not in item
                for limit in (0, 17):
                    refused = await client.call_tool(
                        "list_program_runs", {"limit": limit}, raise_on_error=False
                    )
                    assert refused.is_error
                detail = await client.call_tool(
                    "get_program_run", {"run_id": retained[0]}
                )
                assert detail.structured_content is not None
                assert (
                    detail.structured_content["last_observation"]["screen_text"]
                    == "retained"
                )
                assert all(
                    len(error.encode()) <= 2048
                    for error in detail.structured_content["cleanup_errors"]
                )
                await client.call_tool("cleanup_program", {"run_id": retained[1]})
                retained.pop(1)
                second = (
                    await client.call_tool(
                        "list_program_runs",
                        {"limit": 2, "cursor": first["next_cursor"]},
                    )
                ).structured_content
                assert second is not None
                assert [item["run_id"] for item in second["items"]] == retained[1:]
                assert second["next_cursor"] is None
                current = (
                    await client.call_tool("list_program_runs", {})
                ).structured_content
                assert current is not None
                assert [item["run_id"] for item in current["items"]] == retained
            finally:
                for run_id in retained:
                    await client.call_tool("cleanup_program", {"run_id": run_id})
            empty = (await client.call_tool("list_program_runs", {})).structured_content
            assert empty == {"items": [], "next_cursor": None}

    asyncio.run(exercise())


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
@pytest.mark.parametrize("backend", ["asyncio", "anyio"])
def test_program_cancellation_does_not_wait_for_unrelated_executor(
    mcp_session: Session, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """Occupy the shared executor with a real worker and cancel a program request."""
    before = mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout
    reserve = _programs._ProgramRuns.reserve
    execute = _programs._ProgramRuns.execute

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        occupied, reserved, joined = (asyncio.Event() for _ in range(3))
        release = threading.Event()

        def unrelated_work() -> None:
            loop.call_soon_threadsafe(occupied.set)
            release.wait()

        def observed_reserve(owner: _programs._ProgramRuns, *args: t.Any) -> t.Any:
            result = reserve(owner, *args)
            reserved.set()
            return result

        async def observed_execute(
            owner: _programs._ProgramRuns, *args: t.Any, **kwargs: t.Any
        ) -> t.Any:
            try:
                return await execute(owner, *args, **kwargs)
            finally:
                joined.set()

        monkeypatch.setattr(_programs._ProgramRuns, "reserve", observed_reserve)
        monkeypatch.setattr(_programs._ProgramRuns, "execute", observed_execute)
        async with Client(_application()) as client:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                loop.set_default_executor(executor)
                limiter = anyio.to_thread.current_default_thread_limiter()
                old_tokens = limiter.total_tokens
                limiter.total_tokens = 1
                blocker = (
                    loop.run_in_executor(None, unrelated_work)
                    if backend == "asyncio"
                    else asyncio.create_task(anyio.to_thread.run_sync(unrelated_work))
                )
                task = None
                try:
                    await asyncio.wait_for(occupied.wait(), 1)
                    task = asyncio.create_task(
                        client.call_tool(
                            "run_program",
                            {
                                "session_id": mcp_session.session_id,
                                "socket_name": mcp_session.server.socket_name,
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "import signal; signal.pause()",
                                ],
                            },
                        )
                    )
                    await asyncio.wait_for(reserved.wait(), 1)
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                    await asyncio.wait_for(joined.wait(), 1)
                    assert not blocker.done()
                    assert (
                        await client.call_tool("list_program_runs", {})
                    ).data.items == []
                finally:
                    release.set()
                    await blocker
                    limiter.total_tokens = old_tokens
                    if task is not None:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                    if reserved.is_set():
                        await asyncio.wait_for(joined.wait(), 1)

    asyncio.run(exercise())
    assert mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout == before


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
def test_program_thread_start_failure_cannot_execute_later(
    mcp_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Refuse a real thread start, then prove later work cannot drain a lost launch."""
    marker = tmp_path / "unexpected-launch"

    def unavailable(thread: threading.Thread) -> t.NoReturn:
        msg = "Native worker thread creation is unavailable"
        raise RuntimeError(msg)

    async def exercise() -> None:
        async with Client(_application()) as client:
            with monkeypatch.context() as patch:
                patch.setattr(threading.Thread, "start", unavailable)
                refused = await client.call_tool(
                    "run_program",
                    {
                        "session_id": mcp_session.session_id,
                        "socket_name": mcp_session.server.socket_name,
                        "argv": [
                            sys.executable,
                            "-c",
                            "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                            str(marker),
                        ],
                    },
                    raise_on_error=False,
                )
            assert refused.is_error
            assert (await client.call_tool("list_program_runs", {})).data.items == []
            await client.call_tool(
                "get_server_capabilities",
                {"socket_name": mcp_session.server.socket_name},
            )
            assert not marker.exists()
            assert (await client.call_tool("list_program_runs", {})).data.items == []

    asyncio.run(exercise())


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
@pytest.mark.parametrize("boundary", ["running", "admission", "cleanup", "disconnect"])
def test_program_request_cancellation_settles_worker(
    mcp_session: Session,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    """Native FIFO readiness and explicit admission/cleanup barriers expose races."""
    fifo = tmp_path / "ready"
    os.mkfifo(fifo, 0o600)
    descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    before = mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout
    run = Session.run_program
    execute = _programs._ProgramRuns.execute

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        ready, entered, cleaned, joined = (asyncio.Event() for _ in range(4))
        admission, release_cleanup = threading.Event(), threading.Event()
        cancellation: list[threading.Event] = []
        request_ids: list[str | int] = []
        if boundary != "admission":
            admission.set()
        if boundary != "cleanup":
            release_cleanup.set()

        def readable() -> None:
            if os.read(descriptor, 64):
                ready.set()
                loop.remove_reader(descriptor)

        def controlled(owner: Session, *args: t.Any, **kwargs: t.Any) -> t.Any:
            if request_ids:
                return run(owner, *args, **kwargs)
            context = get_context().request_context
            assert context is not None
            assert context.request_id is not None
            request_ids.append(context.request_id)
            cancellation.append(kwargs["cancel_event"])
            loop.call_soon_threadsafe(entered.set)
            try:
                assert admission.wait(1), "Admission barrier was not released"
                return run(owner, *args, **kwargs)
            finally:
                loop.call_soon_threadsafe(cleaned.set)
                assert release_cleanup.wait(1), "Cleanup barrier was not released"

        async def observed(
            owner: _programs._ProgramRuns, *args: t.Any, **kwargs: t.Any
        ) -> t.Any:
            context = get_context().request_context
            assert context is not None
            try:
                return await execute(owner, *args, **kwargs)
            finally:
                if request_ids and context.request_id == request_ids[0]:
                    joined.set()

        monkeypatch.setattr(Session, "run_program", controlled)
        monkeypatch.setattr(_programs._ProgramRuns, "execute", observed)
        if boundary == "admission":
            monkeypatch.setattr(_programs, "_MAX_RUNS", 1)
        loop.add_reader(descriptor, readable)
        app = (
            "import os,signal; f=os.open(os.environ['READY_FIFO'],os.O_WRONLY); "
            "os.write(f,b'R'); os.close(f); signal.pause()"
        )
        async with contextlib.AsyncExitStack() as stack:
            client = await stack.enter_async_context(Client(_application()))
            task = asyncio.create_task(
                client.call_tool(
                    "run_program",
                    {
                        "session_id": mcp_session.session_id,
                        "socket_name": mcp_session.server.socket_name,
                        "argv": [sys.executable, "-c", app],
                        "environment": {"READY_FIFO": str(fifo)},
                    },
                )
            )
            try:
                async with asyncio.timeout(1):
                    await (entered if boundary == "admission" else ready).wait()
                    records = await client.call_tool("list_program_runs", {})
                assert len(records.data.items) == 1
                if boundary == "admission":
                    marker = tmp_path / "capacity-bypass"
                    refused = await client.call_tool(
                        "run_program",
                        {
                            "session_id": mcp_session.session_id,
                            "socket_name": mcp_session.server.socket_name,
                            "argv": [
                                sys.executable,
                                "-c",
                                "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()",
                                str(marker),
                            ],
                        },
                        raise_on_error=False,
                    )
                    assert refused.is_error
                    assert "capacity is full" in refused.content[0].text
                    assert not marker.exists()
                if boundary == "disconnect":
                    async with asyncio.timeout(1):
                        await stack.aclose()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    client = await stack.enter_async_context(Client(_application()))
                else:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                async with asyncio.timeout(1):
                    await client.call_tool("list_program_runs", {})
                assert cancellation[0].is_set()
                if boundary == "admission":
                    assert not ready.is_set()
                    admission.set()
                if boundary == "cleanup":
                    async with asyncio.timeout(1):
                        await cleaned.wait()
                    await client.session.send_notification(
                        CancelledNotification(
                            params=CancelledNotificationParams(
                                request_id=request_ids[0],
                                reason="Repeated cleanup cancellation",
                            )
                        )
                    )
                    async with asyncio.timeout(1):
                        await client.call_tool("list_program_runs", {})
                    assert not joined.is_set()
                    release_cleanup.set()
                async with asyncio.timeout(1):
                    await joined.wait()
                assert (
                    await client.call_tool("list_program_runs", {})
                ).data.items == []
            finally:
                admission.set()
                release_cleanup.set()
                for event in cancellation:
                    event.set()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                if entered.is_set():
                    async with asyncio.timeout(1):
                        await joined.wait()
                loop.remove_reader(descriptor)

    try:
        asyncio.run(exercise())
    finally:
        os.close(descriptor)
    assert mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout == before


@pytest.mark.skipif(
    not has_gte_version("3.8"), reason="owned programs require tmux 3.8"
)
def test_program_missing_receipt_keeps_unknown_owner(
    mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lose a real creation receipt; the private server fixture owns final disposal."""
    store = _programs._ProgramRuns()
    monkeypatch.setattr(program_tools, "_program_runs", store)
    native = program.Program._run
    before = mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout

    def lose_receipt(
        owner: program.Program, args: tuple[str, ...], *rest: t.Any, **kwargs: t.Any
    ) -> bytes:
        result = native(owner, args, *rest, **kwargs)
        if args[0] == "if-shell" and any("new-window" in arg for arg in args):
            return b""
        return result

    async def exercise() -> None:
        async with Client(_application()) as client:
            with monkeypatch.context() as patch:
                patch.setattr(program.Program, "_run", lose_receipt)
                result = await client.call_tool(
                    "run_program",
                    {
                        "session_id": mcp_session.session_id,
                        "socket_name": mcp_session.server.socket_name,
                        "argv": [sys.executable, "-c", "raise SystemExit(7)"],
                    },
                    raise_on_error=False,
                )
            assert result.is_error
            run_id = (result.meta or {})["program_recovery_id"]
            (record,) = (await client.call_tool("list_program_runs", {})).data.items
            assert record.pane_state == "unverified"
            assert record.pane_id is None and record.cleanup_pending
            cleanup = await client.call_tool(
                "cleanup_program", {"run_id": run_id}, raise_on_error=False
            )
            assert cleanup.is_error
            assert (cleanup.meta or {})["program_recovery_id"] == run_id
            assert (
                len((await client.call_tool("list_program_runs", {})).data.items) == 1
            )

    asyncio.run(exercise())
    panes = mcp_session.cmd("list-panes", "-s", "-F", "#{pane_id}").stdout
    assert set(before) < set(panes) and len(panes) == len(before) + 1
