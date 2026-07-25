"""SEP-1686 task execution for ``wait_for_text``.

``wait_for_text`` is the only tool in this server that blocks for a
caller-chosen duration, so it is the only candidate for out-of-band
execution. These tests pin what task mode actually buys and — more
importantly — what it costs, because the costs are not visible from the
tool signature.

Everything here runs against a real pane through a real MCP client
(``FastMCPTransport``), not by calling the tool function directly: task
execution only exists at the protocol layer, so a direct call would
exercise none of it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import shutil
import signal
import subprocess
import time
import typing as t
import uuid

import fastmcp
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import FastMCPTransport
from libtmux.server import Server as LibtmuxServer

from libtmux_mcp._utils import _server_cache
from libtmux_mcp.tools import pane_tools

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server


NEVER_MATCHES = "ZZ_NO_SUCH_TEXT_ZZ"

#: ``argv[0]`` the hang shim renames itself to. Unique so a process
#: scan cannot mistake an unrelated ``sleep`` for the tool's child.
_REAP_PROBE_TOKEN = "libtmuxmcp_reap_probe"


@pytest.fixture
def task_client(monkeypatch: pytest.MonkeyPatch) -> t.Callable[[], Client[t.Any]]:
    """Return a factory for a task-capable in-process MCP client.

    Each test gets its own Docket queue name. The backend is
    ``memory://``, whose state is process-global, so a shared name would
    let one test's queued waits occupy the next test's worker slots.
    """
    monkeypatch.setattr(fastmcp.settings.docket, "name", f"test-{uuid.uuid4().hex[:8]}")

    def factory() -> Client[t.Any]:
        mcp = FastMCP(name="test-wait-tasks")
        pane_tools.register(mcp)
        return Client(FastMCPTransport(mcp))

    return factory


def test_task_mode_returns_the_same_result_as_a_blocking_call(
    mcp_server: Server, mcp_pane: Pane, task_client: t.Callable[[], Client[t.Any]]
) -> None:
    """The wait's guarantees are enforced in the tool body, not the call mode.

    ``mode="optional"`` only changes *when* the caller is handed the
    result; the poll loop, the delta filter and the outcome enum are
    the same code either way. This asserts that empirically against one
    pane rather than trusting that reading.
    """

    async def emit(marker: str) -> None:
        await asyncio.sleep(0.2)
        await asyncio.to_thread(mcp_pane.send_keys, f"echo {marker}", True)

    async def run() -> tuple[t.Any, t.Any]:
        async with task_client() as client:
            sync_emit = asyncio.create_task(emit("TASKS_SYNC_MARKER"))
            sync_result = await client.call_tool(
                "wait_for_text",
                {
                    "patterns": ["TASKS_SYNC_MARKER"],
                    "pane_id": mcp_pane.pane_id,
                    "timeout": 5.0,
                    "socket_name": mcp_server.socket_name,
                },
            )
            await sync_emit

            task_emit = asyncio.create_task(emit("TASKS_TASK_MARKER"))
            handle = await client.call_tool(
                "wait_for_text",
                {
                    "patterns": ["TASKS_TASK_MARKER"],
                    "pane_id": mcp_pane.pane_id,
                    "timeout": 5.0,
                    "socket_name": mcp_server.socket_name,
                },
                task=True,
            )
            assert handle.returned_immediately is False, (
                "server declined the task and degraded to a blocking call; "
                "the rest of this test would then prove nothing"
            )
            task_result = await handle.result()
            await task_emit
            return sync_result.data, task_result.data

    sync_data, task_data = asyncio.run(run())

    assert sync_data.found is True
    assert task_data.found is True
    assert sync_data.outcome == task_data.outcome == "matched"
    assert task_data.matched_index == 0
    assert any("TASKS_TASK_MARKER" in line for line in task_data.matched_lines)
    assert task_data.pane_id == mcp_pane.pane_id


def test_task_mode_still_enforces_the_wait_ceiling(
    mcp_server: Server,
    mcp_pane: Pane,
    task_client: t.Callable[[], Client[t.Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running out-of-band does not buy the caller a longer wait.

    The obvious argument for relaxing the ceiling under tasks is that a
    background wait is not holding the connection. It is holding
    something scarcer — see
    :func:`test_task_mode_waits_queue_behind_the_worker_concurrency_cap`
    — so the clamp stays, and ``effective_timeout`` still reports it.
    """
    from libtmux_mcp import _wait_policy

    monkeypatch.setattr(_wait_policy, "_wait_max_seconds", 1.0)

    async def run() -> t.Any:
        async with task_client() as client:
            handle = await client.call_tool(
                "wait_for_text",
                {
                    "patterns": [NEVER_MATCHES],
                    "pane_id": mcp_pane.pane_id,
                    "timeout": 90.0,
                    "socket_name": mcp_server.socket_name,
                },
                task=True,
            )
            return (await handle.result()).data

    data = asyncio.run(run())
    assert data.effective_timeout == 1.0
    assert data.outcome == "timeout"
    assert data.elapsed_seconds < 5.0


def test_task_mode_waits_queue_behind_the_worker_concurrency_cap(
    mcp_server: Server,
    mcp_pane: Pane,
    task_client: t.Callable[[], Client[t.Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task mode puts a queue in front of the wait.

    The wait's clock does not start until the queue lets it through.

    A blocking ``wait_for_text`` starts polling the moment the request
    lands. A task-mode one starts polling when a Docket worker slot
    frees, and ``FASTMCP_DOCKET_CONCURRENCY`` defaults to 10 — so an
    agent that fans out eleven background waits gets an eleventh whose
    baseline is snapshotted a full ceiling late. Output that arrived
    while it was queued is then *pre-existing scrollback* to it, which
    the delta filter is required to suppress: the wait reports
    ``found=false`` for text that did appear.

    Nothing in ``execution: {taskSupport: optional}`` tells a caller
    this. The cap is squashed to 2 here so the failure is cheap to
    reproduce; the shape is identical at 10.
    """
    monkeypatch.setattr(fastmcp.settings.docket, "concurrency", 2)

    async def run() -> tuple[float, t.Any]:
        async with task_client() as client:
            started = time.monotonic()
            fillers = [
                await client.call_tool(
                    "wait_for_text",
                    {
                        "patterns": [NEVER_MATCHES],
                        "pane_id": mcp_pane.pane_id,
                        "timeout": 2.0,
                        "socket_name": mcp_server.socket_name,
                    },
                    task=True,
                )
                for _ in range(2)
            ]
            victim = await client.call_tool(
                "wait_for_text",
                {
                    "patterns": [NEVER_MATCHES],
                    "pane_id": mcp_pane.pane_id,
                    "timeout": 2.0,
                    "socket_name": mcp_server.socket_name,
                },
                task=True,
            )
            submitted = time.monotonic() - started
            assert submitted < 1.0, (
                "submission itself blocked, so a late finish below would "
                "not prove the wait was queued"
            )
            data = (await victim.result()).data
            wall = time.monotonic() - started
            await asyncio.gather(*(f.result() for f in fillers))
            return wall, data

    wall, data = asyncio.run(run())

    # The wait believes it ran for its full 2 s and no longer.
    assert data.outcome == "timeout"
    assert data.elapsed_seconds == pytest.approx(2.0, abs=0.6)
    # The caller waited for two of them, back to back.
    assert wall > 3.0, (
        f"third task finished in {wall:.2f}s; expected it to queue behind "
        "the two occupying the worker's only slots"
    )


def _hung_capture_pids() -> list[int]:
    """Return pids of live hung ``capture-pane`` stand-ins, excluding us.

    ``pgrep -f`` would match this test process's own command line, so
    only ``argv[0]``'s basename is compared, and our own pid skipped.
    The shim execs ``sleep`` through a uniquely named symlink, so the
    token cannot appear anywhere but a child the tool actually spawned.
    """
    listing = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True, check=True
    ).stdout
    me = os.getpid()
    pids: list[int] = []
    for line in listing.splitlines():
        pid_field, _, args = line.strip().partition(" ")
        if not pid_field.isdigit() or int(pid_field) == me:
            continue
        argv = args.split()
        if argv and pathlib.Path(argv[0]).name == _REAP_PROBE_TOKEN:
            pids.append(int(pid_field))
    return pids


def test_task_cancel_kills_the_tmux_child(
    mcp_server: Server,
    mcp_pane: Pane,
    task_client: t.Callable[[], Client[t.Any]],
    tmp_path: pathlib.Path,
) -> None:
    """``tasks/cancel`` must reap the wait's tmux child, like a plain cancel.

    Task execution runs the tool body on a Docket worker rather than
    directly on the request's asyncio task, so the cancellation reaches
    the poll loop by a different route than the ``notifications/
    cancelled`` path that ``_tmux_proc._run_tmux_bounded``'s kill-and-
    reap guard was written for. This asserts the guard is still on the
    path — the failure mode it prevents is a tmux child that outlives
    the cancellation for the rest of its per-call budget.

    Real ``capture-pane`` calls finish in milliseconds and are never
    caught alive by a process scan, so ``tmux_bin`` points at a shim
    that hangs on ``capture-pane`` under a unique ``argv[0]``.
    """
    real_tmux = shutil.which("tmux")
    real_sleep = shutil.which("sleep")
    assert real_tmux is not None
    assert real_sleep is not None
    # A symlink rather than ``exec -a``: /bin/sh is dash on Debian and
    # has no ``-a``, and the link name becomes argv[0] for free.
    hang = tmp_path / _REAP_PROBE_TOKEN
    hang.symlink_to(real_sleep)
    shim = tmp_path / "tmux-hang"
    shim.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "capture-pane" ]; then\n'
        f"    exec {hang} 300\n"
        "  fi\n"
        "done\n"
        f'exec {real_tmux} "$@"\n'
    )
    shim.chmod(0o755)

    assert not _hung_capture_pids(), "a stale probe child is already running"

    shim_server = LibtmuxServer(socket_name=mcp_server.socket_name, tmux_bin=str(shim))
    _server_cache[(mcp_server.socket_name, None, None)] = shim_server
    _server_cache[(None, None, None)] = shim_server

    async def run() -> list[int]:
        async with task_client() as client:
            handle = await client.call_tool(
                "wait_for_text",
                {
                    "patterns": [NEVER_MATCHES],
                    "pane_id": mcp_pane.pane_id,
                    "timeout": 30.0,
                    "socket_name": mcp_server.socket_name,
                },
                task=True,
            )
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not _hung_capture_pids():
                await asyncio.sleep(0.05)
            assert _hung_capture_pids(), (
                "no hung tmux child observed before the cancel — the probe "
                "is broken, so a later 'no survivors' result is vacuous"
            )

            status = await client.cancel_task(handle.task_id)
            assert status.status == "cancelled"

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not _hung_capture_pids():
                    break
                await asyncio.sleep(0.05)
            return _hung_capture_pids()

    try:
        survivors = asyncio.run(run())
    finally:
        for pid in _hung_capture_pids():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)

    assert not survivors, (
        f"cancelled task-mode wait orphaned tmux child(ren) {survivors}; "
        "task execution bypassed _run_tmux_bounded's kill-and-reap"
    )
