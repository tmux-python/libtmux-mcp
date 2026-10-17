"""Version checks for MCP-owned tmux subprocesses, replacement and cancellation."""

from __future__ import annotations

import asyncio
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time
import types

import pytest
from libtmux import exc

from libtmux_mcp import _tmux_proc


@pytest.fixture(autouse=True)
def _fresh_version_cache() -> None:
    """Start each test without a remembered release."""
    _tmux_proc._VERSION_CACHE.clear()


def _binary(variable: str, expected: str) -> str:
    value = os.environ.get(variable)
    if value is None:
        pytest.skip(f"set {variable} to native tmux {expected}")
    report = subprocess.run([value, "-V"], capture_output=True, timeout=5, check=True)
    assert report.stdout == f"tmux {expected}\n".encode()
    return value


def _native(
    binary: str, socket: pathlib.Path, *argv: str
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [binary, "-N", "-S", str(socket), *argv],
        capture_output=True,
        timeout=5,
        check=False,
    )


def _start(binary: str, socket: pathlib.Path) -> None:
    result = subprocess.run(
        [
            binary,
            "-f",
            "/dev/null",
            "-S",
            str(socket),
            "new-session",
            "-d",
            "exec sleep 120",
        ],
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["tmux with spaces", " tmux with spaces"])
def test_native_minimum_owned_subprocesses(tmp_path: pathlib.Path, name: str) -> None:
    """Both owned lanes retain native output and a literal trailing semicolon."""
    binary = _binary("LIBTMUX_TEST_MINIMUM_TMUX", "3.3a")
    renamed = tmp_path / name
    renamed.symlink_to(binary)
    socket = tmp_path / "s"
    try:
        result = _tmux_proc._run_tmux_sync_bounded(
            [str(renamed), "-S", str(socket), "new-session", "-d", "exec sleep 120"],
            timeout=3,
        )
        assert result.returncode == 0
        argv = [str(renamed), "-S", str(socket), "display-message", "-p", "literal;"]
        assert (
            _tmux_proc._run_tmux_sync_bounded(argv, timeout=3).stdout == b"literal;\n"
        )
        assert asyncio.run(_tmux_proc._run_tmux_bounded(argv, timeout=3)) == (
            0,
            b"literal;\n",
            b"",
        )
    finally:
        _native(binary, socket, "kill-server")


@pytest.mark.parametrize("source", ["minimum", "path"])
def test_version_check_fires_no_user_hooks(tmp_path: pathlib.Path, source: str) -> None:
    """Neither owned lane lets the version probe run a user's hooks."""
    if source == "minimum":
        binary = os.environ.get("LIBTMUX_TEST_MINIMUM_TMUX")
    else:
        binary = shutil.which("tmux")
    if binary is None:
        pytest.skip(f"no tmux for the {source} lane")
    socket = tmp_path / "s"
    _start(binary, socket)

    def native(*argv: str) -> subprocess.CompletedProcess[bytes]:
        return _native(binary, socket, *argv)

    try:
        assert (
            native("set-hook", "-g", "after-display-message", "set -ag @a x").returncode
            == 0
        )
        # command-error only exists on 3.5+; older releases refuse the hook.
        has_error = (
            native("set-hook", "-g", "command-error", "set -ag @e y").returncode == 0
        )
        argv = [binary, "-S", str(socket), "list-sessions"]
        _tmux_proc._run_tmux_sync_bounded(argv, timeout=3)
        asyncio.run(_tmux_proc._run_tmux_bounded(argv, timeout=3))

        # Hooks run in order, so a control fired after the probes shows that
        # none of them left a mark: each sentinel holds exactly one letter.
        native("display-message", "-p", "control")
        if has_error:
            assert native("kill-session", "-t", "=missing").returncode != 0
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            after = native("show-options", "-gqv", "@a").stdout.strip()
            error = native("show-options", "-gqv", "@e").stdout.strip()
            if after == b"x" and (error == b"y" or not has_error):
                break
            time.sleep(0.05)
        assert native("show-options", "-gqv", "@a").stdout.strip() == b"x"
        if has_error:
            assert native("show-options", "-gqv", "@e").stdout.strip() == b"y"
    finally:
        native("kill-server")


@pytest.mark.parametrize(
    ("variable", "release"),
    [
        ("LIBTMUX_TEST_UNSUPPORTED_TMUX", "3.2a"),
        ("LIBTMUX_TEST_UNPATCHED_TMUX", "3.3"),
    ],
)
def test_old_daemon_refuses_owned_writes(
    tmp_path: pathlib.Path, variable: str, release: str
) -> None:
    """A newer executable cannot authorize writes on an older live daemon."""
    binary = _binary("LIBTMUX_TEST_MINIMUM_TMUX", "3.3a")
    old = _binary(variable, release)
    socket = tmp_path / "s"
    _start(old, socket)
    argv = [binary, "-S", str(socket), "set-option", "-g", "@forbidden", "changed"]
    try:
        with pytest.raises(exc.VersionTooLow):
            _tmux_proc._run_tmux_sync_bounded(argv, timeout=3)
        with pytest.raises(exc.VersionTooLow):
            asyncio.run(_tmux_proc._run_tmux_bounded(argv, timeout=3))
        assert _native(old, socket, "show-options", "-gqv", "@forbidden").stdout == b""
    finally:
        _native(old, socket, "kill-server")


@pytest.mark.parametrize(
    ("variable", "release"),
    [
        ("LIBTMUX_TEST_UNSUPPORTED_TMUX", "3.2a"),
        ("LIBTMUX_TEST_UNPATCHED_TMUX", "3.3"),
    ],
)
def test_old_executable_does_not_start_an_endpoint(
    tmp_path: pathlib.Path, variable: str, release: str
) -> None:
    """A cold version check preserves patch letters before new-session can run."""
    old = _binary(variable, release)
    socket = tmp_path / "s"
    argv = [old, "-S", str(socket), "new-session", "-d", "exec sleep 120"]
    with pytest.raises(exc.VersionTooLow):
        _tmux_proc._run_tmux_sync_bounded(argv, timeout=3)
    with pytest.raises(exc.VersionTooLow):
        asyncio.run(_tmux_proc._run_tmux_bounded(argv, timeout=3))
    assert not socket.exists()


def test_incompatible_backend_refuses_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An uncoordinated package cannot authorize either owned process lane."""
    monkeypatch.setitem(
        sys.modules,
        "libtmux._internal.support",
        types.ModuleType("libtmux._internal.support"),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("incompatible backend reached subprocess creation")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(exc.LibTmuxException, match="Unsupported libtmux backend"):
        _tmux_proc._run_tmux_sync_bounded(["tmux", "list-sessions"], timeout=1)
    with pytest.raises(exc.LibTmuxException, match="Unsupported libtmux backend"):
        asyncio.run(_tmux_proc._run_tmux_bounded(["tmux", "list-sessions"], timeout=1))


def test_probe_failure_is_a_library_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed probe is an expected tool error, not an unexpected RuntimeError."""

    async def failing(argv: list[str], *, timeout: float) -> tuple[int, bytes, bytes]:
        return 1, b"", b"permission denied\n"

    monkeypatch.setattr(_tmux_proc, "_run_tmux_unchecked", failing)
    with pytest.raises(exc.LibTmuxException, match="permission denied"):
        asyncio.run(_tmux_proc._run_tmux_bounded(["tmux", "list-sessions"], timeout=1))

    def failing_sync(
        argv: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", b"permission denied\n")

    monkeypatch.setattr(subprocess, "run", failing_sync)
    with pytest.raises(exc.LibTmuxException, match="permission denied"):
        _tmux_proc._run_tmux_sync_bounded(["tmux", "list-sessions"], timeout=1)


def test_replacement_after_probe_preserves_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final native guard refuses a replacement before the requested effect."""
    binary = _binary("LIBTMUX_TEST_MINIMUM_TMUX", "3.3a")
    old = _binary("LIBTMUX_TEST_UNSUPPORTED_TMUX", "3.2a")
    socket = tmp_path / "s"
    _start(binary, socket)
    original = asyncio.create_subprocess_exec
    replaced = False

    async def replace(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        nonlocal replaced
        if "set-option" in argv:
            assert not replaced
            replaced = True
            _native(binary, socket, "kill-server")
            _start(old, socket)
        return await original(*argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", replace)
    try:
        argv = [binary, "-S", str(socket), "set-option", "-g", "@forbidden", "changed"]
        with pytest.raises(exc.VersionTooLow):
            asyncio.run(_tmux_proc._run_tmux_bounded(argv, timeout=3))
        assert replaced
        assert _native(old, socket, "show-options", "-gqv", "@forbidden").stdout == b""
    finally:
        _native(binary, socket, "kill-server")
        _native(old, socket, "kill-server")


@pytest.mark.parametrize("cancel", [False, True])
def test_version_check_budget_and_cancellation_reap_child(
    tmp_path: pathlib.Path, cancel: bool
) -> None:
    """Slow metadata consumes the same budget and remains owned on cancellation."""
    wrapper = tmp_path / "blocked-probe"
    pid_file = tmp_path / "pid"
    wrapper.write_text(
        f'#!/bin/sh\nprintf "%s" "$$" > {shlex.quote(str(pid_file))}\nexec sleep 120\n'
    )
    wrapper.chmod(0o700)

    async def drive() -> None:
        started = time.monotonic()
        task = asyncio.create_task(
            _tmux_proc._run_tmux_bounded(
                [str(wrapper), "list-sessions"], timeout=0.2 if not cancel else 3
            )
        )
        if cancel:
            deadline = time.monotonic() + 2
            while not pid_file.exists() or not pid_file.read_text():
                assert time.monotonic() < deadline, "metadata child did not start"
                await asyncio.sleep(0.005)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
        assert time.monotonic() - started < 1.5
        pid = int(pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(drive())


def test_checked_release_is_probed_once_per_endpoint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Later calls reuse the release; a refusal or a gone daemon probes again."""
    binary = _binary("LIBTMUX_TEST_MINIMUM_TMUX", "3.3a")
    socket = tmp_path / "s"
    _start(binary, socket)
    probes: list[list[str]] = []
    real = subprocess.run

    def counted(argv: list[str], *args: object, **kwargs: object) -> object:
        if "run-shell" in argv and "-C" in argv:
            probes.append(argv)
        return real(argv, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(subprocess, "run", counted)
    argv = [binary, "-S", str(socket), "list-sessions"]
    try:
        for _ in range(3):
            _tmux_proc._run_tmux_sync_bounded(argv, timeout=3)
        assert asyncio.run(_tmux_proc._run_tmux_bounded(argv, timeout=3))[0] == 0
        assert len(probes) == 1
        _native(binary, socket, "kill-server")
        # The cached daemon is gone: the call checks the version again instead
        # of failing with a stale release, and the endpoint is not cached while
        # absent.
        result = _tmux_proc._run_tmux_sync_bounded(argv, timeout=3, check=False)
        assert result.returncode != 0
        assert not _tmux_proc._VERSION_CACHE
    finally:
        _native(binary, socket, "kill-server")


def test_stale_cached_release_is_refused_and_forgotten(tmp_path: pathlib.Path) -> None:
    """A cached release that no longer matches the daemon fails the fence."""
    binary = _binary("LIBTMUX_TEST_MINIMUM_TMUX", "3.3a")
    socket = tmp_path / "s"
    _start(binary, socket)
    argv = [binary, "-S", str(socket), "set-option", "-g", "@stale", "ran"]
    try:
        _tmux_proc._VERSION_CACHE[
            _tmux_proc._endpoint_key(binary, ["-S", str(socket)])
        ] = "3.99"
        with pytest.raises(exc.VersionTooLow):
            _tmux_proc._run_tmux_sync_bounded(argv, timeout=3)
        assert not _tmux_proc._VERSION_CACHE
        assert _native(binary, socket, "show-options", "-gqv", "@stale").stdout == b""
    finally:
        _native(binary, socket, "kill-server")
