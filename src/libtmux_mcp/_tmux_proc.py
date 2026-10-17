"""Cancellable, wall-clock-bounded ``tmux`` invocation.

Every tool that blocks for a caller-chosen duration needs the same two
guarantees from its ``tmux`` child: it must not outlive its budget, and
it must not outlive a cancelled call. Neither libtmux's ``tmux_cmd``
(``Popen.communicate()`` with no timeout) nor
``asyncio.to_thread(subprocess.run, ..., timeout=...)`` provides the
second one — ``to_thread`` hands the coroutine a ``CancelledError``
immediately while the worker thread stays blocked in the untimed
``waitpid``, so the child runs on for the rest of its budget with
nobody waiting for it. Measured on ``tmux wait-for``: a 15 s wait
cancelled at 2 s left the child alive for another 13 s.

A worker thread is also unrecoverable at shutdown:
``concurrent.futures.thread._python_exit`` — registered through
``threading._register_atexit`` — joins every pool worker with no
timeout, so one wedged tmux hangs interpreter exit forever, and no
thread-based arrangement avoids it (a private pool with
``shutdown(wait=False)`` is joined by that same hook).

A subprocess we own can simply be killed, so this module owns it.
"""

from __future__ import annotations

import asyncio
import contextlib
import getopt
import importlib
import os
import subprocess
import time
import types
import typing as t
import uuid

from libtmux import exc

#: Bound on the post-kill reap. Short because it is best effort: the
#: loop's child watcher reaps the pid whether or not we wait.
_TMUX_REAP_SECONDS = 0.5


async def _kill_and_reap(
    proc: asyncio.subprocess.Process, task: asyncio.Future[t.Any]
) -> None:
    """Kill a tmux child and tear down its reader without deadlocking.

    Order matters. Killing first lets the reader's cancellation
    actually complete; cancelling first leaves a live process whose
    pipes may be held open by a grandchild, and the reader then never
    finishes. The final reap is bounded for the same reason —
    ``proc.wait()`` can block indefinitely when something other than
    the process we killed still holds the write ends. The event loop's
    child watcher reaps the pid regardless, so a timeout here leaks
    nothing.

    Parameters
    ----------
    proc : asyncio.subprocess.Process
        The tmux child to kill.
    task : asyncio.Future
        The in-flight ``proc.communicate()`` future reading its pipes.
    """
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=_TMUX_REAP_SECONDS)


async def _run_tmux_unchecked(
    argv: list[str], *, timeout: float
) -> tuple[int, bytes, bytes]:
    """Run one tmux argv under a hard bound, killing it on cancellation.

    Parameters
    ----------
    argv : list of str
        Full tmux command vector, as built by
        :func:`~libtmux_mcp._utils._tmux_argv`.
    timeout : float
        Wall-clock bound in seconds. On expiry the child is killed and
        reaped before ``TimeoutError`` is raised.

    Returns
    -------
    tuple of (int, bytes, bytes)
        ``(returncode, stdout, stderr)``.

    Raises
    ------
    TimeoutError
        When ``timeout`` elapses first.
    asyncio.CancelledError
        Re-raised after the child has been killed and reaped, so a
        cancelled call never orphans tmux.
    """
    deadline = time.monotonic() + timeout
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Deliberately NOT ``wait_for(proc.communicate())``. Measured: a
    # wedged tmux that leaves a grandchild holding the stdout/stderr
    # write ends never reaches EOF, and ``await proc.wait()`` after
    # ``kill()`` then deadlocks — the pipes outlive the process we
    # killed. ``asyncio.wait`` observes the timeout without cancelling,
    # so the kill happens first and the read task is torn down after,
    # when cancelling it can actually succeed.
    task = asyncio.ensure_future(proc.communicate())
    try:
        done, _pending = await asyncio.wait(
            {task}, timeout=max(0, deadline - time.monotonic())
        )
        if not done:
            await _kill_and_reap(proc, task)
            raise TimeoutError
        stdout, stderr = task.result()
    except asyncio.CancelledError:
        # The whole call was cancelled (MCP client hung up). Tear the
        # child down before letting the cancellation through, or tmux
        # is orphaned. The cancellation lands on the ``asyncio.wait``
        # above just as often as on ``task.result()``, so the guard
        # must span both — otherwise a cancel while waiting orphans the
        # child.
        await _kill_and_reap(proc, task)
        raise
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr


_MINIMUM_TMUX = "3.3a"


def _backend() -> types.ModuleType:
    """Require a libtmux that publishes the daemon-first version check."""
    try:
        support = importlib.import_module("libtmux._internal.support")
    except ImportError as error:
        msg = (
            "Unsupported libtmux backend: libtmux 0.63 or newer is required "
            "for the tmux 3.3a version check"
        )
        raise exc.LibTmuxException(msg) from error
    if not all(
        callable(getattr(support, name, None))
        for name in ("require_version", "check_fence")
    ) or not isinstance(getattr(support, "MINIMUM", None), str):
        msg = "Unsupported libtmux backend: the tmux version check is unavailable"
        raise exc.LibTmuxException(msg)
    support.require_version(support.MINIMUM, minimum=_MINIMUM_TMUX)
    return support


def _parts(argv: list[str]) -> tuple[list[str], list[str]]:
    options, commands = getopt.getopt(argv[1:], "L:S:uN")
    selectors: list[str] = []
    for flag, value in options:
        if flag in {"-L", "-S"}:
            selectors.extend((flag, value))
    return selectors, commands


def _absent(returncode: int, stderr: bytes) -> bool:
    if returncode == 0:
        return False
    detail = stderr.decode("utf-8", errors="backslashreplace")
    return "no server running" in detail or (
        "error connecting to" in detail
        and any(
            reason in detail
            for reason in ("No such file or directory", "Connection refused")
        )
    )


def _version(
    stdout: bytes, stderr: bytes, returncode: int, *, binary: str | None
) -> str:
    report = stdout.decode("utf-8", errors="backslashreplace")
    if binary is not None:
        name = (
            os.fsencode(binary)
            .rsplit(b"/", 1)[-1]
            .decode("utf-8", errors="backslashreplace")
        )
        prefix = next(
            (item for item in (name + " ", "tmux ") if report.startswith(item)),
            None,
        )
        report = report[len(prefix) :] if prefix is not None else ""
    if returncode or not report:
        detail = stderr.decode("utf-8", errors="backslashreplace").strip()
        msg = f"tmux version check failed: {detail or 'no version report'}"
        raise exc.LibTmuxException(msg)
    return report.strip()


def _probe(binary: str, selectors: list[str]) -> tuple[list[str], str]:
    """Build the hook-free live version probe and its private marker.

    ``run-shell`` has no after-hook, and with ``-C`` it fails while parsing its
    own output, so neither ``after-*`` nor ``command-error`` hooks fire.
    """
    marker = "libtmux-mcp-version-" + uuid.uuid4().hex + "-"
    return [binary, *selectors, "-N", "run-shell", "-C", marker + "#{version}"], marker


def _live_version(stderr: bytes, returncode: int, marker: str) -> str:
    detail = stderr.decode("utf-8", errors="backslashreplace")
    prefix = f"unknown command: {marker}"
    reports = [
        line[len(prefix) :] for line in detail.splitlines() if line.startswith(prefix)
    ]
    if returncode != 1 or len(reports) != 1:
        msg = f"tmux version check failed: {detail.strip() or 'no version report'}"
        raise exc.LibTmuxException(msg)
    return reports[0]


def _remaining(deadline: float) -> float:
    result = deadline - time.monotonic()
    if result <= 0:
        raise TimeoutError
    return result


def _fence(argv: list[str], version: str, live: bool) -> tuple[list[str], str | None]:
    _backend().require_version(version, minimum=_MINIMUM_TMUX)
    _, commands = _parts(argv)
    prefix = argv[: len(argv) - len(commands)] if commands else argv
    marker = "libtmux-mcp-version-" + uuid.uuid4().hex
    # These callers select one native command; preserve literal terminal text.
    literal = [item[:-1] + "\\;" if item.endswith(";") else item for item in commands]
    return [
        *prefix,
        "-u",
        *(["-N"] if live else []),
        "if-shell",
        "-F",
        "#{==:#{version}," + version + "}",
        "",
        marker,
        ";",
        *literal,
    ], marker


async def _prepare_bounded(
    argv: list[str], deadline: float
) -> tuple[list[str], str | None]:
    _backend()
    selectors, _commands = _parts(argv)
    probe, marker = _probe(argv[0], selectors)
    code, output, error = await _run_tmux_unchecked(probe, timeout=_remaining(deadline))
    live = not _absent(code, error)
    if live:
        version = _live_version(error, code, marker)
    else:
        code, output, error = await _run_tmux_unchecked(
            [argv[0], "-V"], timeout=_remaining(deadline)
        )
        version = _version(output, error, code, binary=argv[0])
    return _fence(argv, version, live)


async def _run_tmux_bounded(
    argv: list[str], *, timeout: float
) -> tuple[int, bytes, bytes]:
    """Check the version and dispatch within one cancellable process budget."""
    deadline = time.monotonic() + timeout
    prepared, marker = await _prepare_bounded(argv, deadline)
    result = await _run_tmux_unchecked(prepared, timeout=_remaining(deadline))
    _backend().check_fence(result[2].decode("utf-8", errors="backslashreplace"), marker)
    return result


def _run_tmux_sync_bounded(
    argv: list[str], *, timeout: float, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    """Check the version and run a synchronous child within one remaining budget."""
    policy = _backend()
    deadline = time.monotonic() + timeout
    selectors, _commands = _parts(argv)

    def run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.run(command, capture_output=True, timeout=budget, check=False)

    command, probe_marker = _probe(argv[0], selectors)
    probe = run(command)
    live = not _absent(probe.returncode, probe.stderr)
    if live:
        version = _live_version(probe.stderr, probe.returncode, probe_marker)
    else:
        probe = run([argv[0], "-V"])
        version = _version(probe.stdout, probe.stderr, probe.returncode, binary=argv[0])
    prepared, marker = _fence(argv, version, live)
    result = run(prepared)
    policy.check_fence(result.stderr.decode("utf-8", errors="backslashreplace"), marker)
    if check:
        result.check_returncode()
    return result
