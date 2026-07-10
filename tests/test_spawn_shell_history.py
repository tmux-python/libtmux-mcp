"""Functional evidence for history controls on spawned shells."""

from __future__ import annotations

import pathlib
import shlex
import shutil
import typing as t

import pytest
from libtmux.test.retry import retry_until

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server


def _exercise_spawned_shell_history(
    *,
    binary: str,
    arguments: tuple[str, ...],
    config_command: t.Callable[[pathlib.Path], str],
    memory_command: t.Callable[[pathlib.Path], str],
    disk_history: t.Callable[[pathlib.Path, pathlib.Path], pathlib.Path],
    sentinel: str,
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> tuple[str, str, str | None]:
    """Run one controlled interactive shell and return its history evidence."""
    from libtmux_mcp.tools.window_tools import split_window

    executable = shutil.which(binary)
    if executable is None:
        pytest.skip(f"{binary} is unavailable; shell history evidence requires it")

    home = tmp_path / f"{binary}-home"
    data_home = tmp_path / f"{binary}-data"
    home.mkdir()
    data_home.mkdir()
    config_path = tmp_path / f"{binary}-configured.txt"
    memory_path = tmp_path / f"{binary}-memory.txt"
    shell = shlex.join((executable, *arguments))

    window = mcp_pane.window
    window.cmd("set-option", "-w", "remain-on-exit", "on")
    pane_info = split_window(
        pane_id=mcp_pane.pane_id,
        shell=shell,
        socket_name=mcp_server.socket_name,
        environment={"HOME": str(home), "XDG_DATA_HOME": str(data_home)},
        suppress_history=True,
    )
    assert pane_info.pane_id is not None
    pane = mcp_server.panes.get(pane_id=pane_info.pane_id, default=None)
    assert pane is not None
    try:

        def _shell_is_ready() -> bool:
            pane.refresh()
            return pane.pane_current_command == binary

        retry_until(_shell_is_ready, 3, raises=True)
        pane.send_keys(config_command(config_path), enter=True)
        retry_until(config_path.exists, 3, raises=True)
        pane.send_keys(
            f"printf '%s\\n' {shlex.quote(sentinel)} >/dev/null",
            enter=True,
        )
        pane.send_keys(memory_command(memory_path), enter=True)
        retry_until(memory_path.exists, 3, raises=True)
        pane.send_keys("exit", enter=True)

        def _pane_is_dead() -> bool:
            rendered = pane.cmd("display-message", "-p", "#{pane_dead}").stdout
            return bool(rendered) and rendered[0].strip() == "1"

        retry_until(_pane_is_dead, 3, raises=True)
        disk_path = disk_history(home, data_home)
        disk_text = disk_path.read_text() if disk_path.exists() else None
        return config_path.read_text(), memory_path.read_text(), disk_text
    finally:
        pane.kill()
        window.cmd("set-option", "-wu", "remain-on-exit")


def test_spawned_bash_configures_disk_suppression_with_memory_caveat(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """Bash receives controls, writes no disk history, but retains memory."""
    sentinel = "BASH_SPAWN_HISTORY_SENTINEL"
    configured, memory, disk = _exercise_spawned_shell_history(
        binary="bash",
        arguments=("--noprofile", "--norc"),
        config_command=lambda path: (
            "printf '%s\\n' \"HISTFILE=<$HISTFILE>\" "
            f'"HISTCONTROL=$HISTCONTROL" > {shlex.quote(str(path))}'
        ),
        memory_command=lambda path: f"history > {shlex.quote(str(path))}",
        disk_history=lambda home, _data: home / ".bash_history",
        sentinel=sentinel,
        mcp_server=mcp_server,
        mcp_pane=mcp_pane,
        tmp_path=tmp_path,
    )

    assert "HISTFILE=<>" in configured
    history_control = configured.split("HISTCONTROL=", 1)[1].strip().split(":")
    assert "ignorespace" in history_control or "ignoreboth" in history_control
    assert sentinel in memory
    assert disk is None or sentinel not in disk


def test_spawned_zsh_configures_disk_suppression_with_memory_caveat(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """Zsh receives an empty HISTFILE, writes no disk history, but keeps memory."""
    sentinel = "ZSH_SPAWN_HISTORY_SENTINEL"
    configured, memory, disk = _exercise_spawned_shell_history(
        binary="zsh",
        arguments=("-f",),
        config_command=lambda path: (
            f"printf '%s\\n' \"HISTFILE=<$HISTFILE>\" > {shlex.quote(str(path))}"
        ),
        memory_command=lambda path: f"fc -l -20 > {shlex.quote(str(path))}",
        disk_history=lambda home, _data: home / ".zsh_history",
        sentinel=sentinel,
        mcp_server=mcp_server,
        mcp_pane=mcp_pane,
        tmp_path=tmp_path,
    )

    assert configured.strip() == "HISTFILE=<>"
    assert sentinel in memory
    assert disk is None or sentinel not in disk


def test_spawned_fish_configures_private_disk_suppression_with_memory_caveat(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """Fish receives private controls, writes no disk history, but keeps memory."""
    sentinel = "FISH_SPAWN_HISTORY_SENTINEL"
    configured, memory, disk = _exercise_spawned_shell_history(
        binary="fish",
        arguments=("--no-config",),
        config_command=lambda path: (
            "printf 'fish_history=<%s>\\nfish_private_mode=<%s>\\n' "
            f'"$fish_history" "$fish_private_mode" > {shlex.quote(str(path))}'
        ),
        memory_command=lambda path: f"history > {shlex.quote(str(path))}",
        disk_history=lambda _home, data: data / "fish" / "fish_history",
        sentinel=sentinel,
        mcp_server=mcp_server,
        mcp_pane=mcp_pane,
        tmp_path=tmp_path,
    )

    assert "fish_history=<>" in configured
    assert "fish_private_mode=<1>" in configured
    assert sentinel in memory
    assert disk is None or sentinel not in disk


def _exercise_shell_startup_override(
    *,
    binary: str,
    configure: t.Callable[
        [pathlib.Path, pathlib.Path],
        tuple[tuple[str, ...], dict[str, str], pathlib.Path],
    ],
    sentinel: str,
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> str | None:
    """Prove a controlled startup file can override the environment policy."""
    from libtmux_mcp.tools.window_tools import split_window

    executable = shutil.which(binary)
    if executable is None:
        pytest.skip(f"{binary} is unavailable; startup override evidence requires it")

    home = tmp_path / f"{binary}-override-home"
    data_home = tmp_path / f"{binary}-override-data"
    home.mkdir()
    data_home.mkdir()
    arguments, extra_environment, disk_path = configure(home, data_home)
    environment = {
        "HOME": str(home),
        "XDG_DATA_HOME": str(data_home),
        **extra_environment,
    }

    window = mcp_pane.window
    window.cmd("set-option", "-w", "remain-on-exit", "on")
    pane_info = split_window(
        pane_id=mcp_pane.pane_id,
        shell=shlex.join((executable, *arguments)),
        socket_name=mcp_server.socket_name,
        environment=environment,
        suppress_history=True,
    )
    assert pane_info.pane_id is not None
    pane = mcp_server.panes.get(pane_id=pane_info.pane_id, default=None)
    assert pane is not None
    try:

        def _shell_is_ready() -> bool:
            pane.refresh()
            return pane.pane_current_command == binary

        retry_until(_shell_is_ready, 3, raises=True)
        pane.send_keys(
            f"printf '%s\\n' {shlex.quote(sentinel)} >/dev/null",
            enter=True,
        )
        pane.send_keys("exit", enter=True)

        def _pane_is_dead() -> bool:
            rendered = pane.cmd("display-message", "-p", "#{pane_dead}").stdout
            return bool(rendered) and rendered[0].strip() == "1"

        retry_until(_pane_is_dead, 3, raises=True)
        return disk_path.read_text() if disk_path.exists() else None
    finally:
        pane.kill()
        window.cmd("set-option", "-wu", "remain-on-exit")


def test_bash_startup_file_can_override_spawn_suppression(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """A Bash rc file can replace HISTFILE after the process starts."""
    sentinel = "BASH_STARTUP_OVERRIDE_SENTINEL"

    def _configure(
        home: pathlib.Path,
        _data_home: pathlib.Path,
    ) -> tuple[tuple[str, ...], dict[str, str], pathlib.Path]:
        history = home / "bash-override-history"
        rcfile = home / ".bashrc"
        rcfile.write_text(
            f"export HISTFILE={shlex.quote(str(history))}\nset -o history\n"
        )
        return ("--noprofile", "--rcfile", str(rcfile)), {}, history

    disk = _exercise_shell_startup_override(
        binary="bash",
        configure=_configure,
        sentinel=sentinel,
        mcp_server=mcp_server,
        mcp_pane=mcp_pane,
        tmp_path=tmp_path,
    )

    assert disk is not None
    assert sentinel in disk


def test_zsh_startup_file_can_override_spawn_suppression(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """A Zsh rc file can replace HISTFILE after the process starts."""
    sentinel = "ZSH_STARTUP_OVERRIDE_SENTINEL"

    def _configure(
        home: pathlib.Path,
        _data_home: pathlib.Path,
    ) -> tuple[tuple[str, ...], dict[str, str], pathlib.Path]:
        history = home / "zsh-override-history"
        (home / ".zshrc").write_text(
            "\n".join(
                (
                    f"HISTFILE={shlex.quote(str(history))}",
                    "HISTSIZE=100",
                    "SAVEHIST=100",
                    "setopt inc_append_history",
                    "",
                )
            )
        )
        return (), {"ZDOTDIR": str(home)}, history

    disk = _exercise_shell_startup_override(
        binary="zsh",
        configure=_configure,
        sentinel=sentinel,
        mcp_server=mcp_server,
        mcp_pane=mcp_pane,
        tmp_path=tmp_path,
    )

    assert disk is not None
    assert sentinel in disk


def test_fish_startup_file_can_override_spawn_suppression(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """A Fish config can replace the history name after process start."""
    sentinel = "FISH_STARTUP_OVERRIDE_SENTINEL"

    def _configure(
        home: pathlib.Path,
        data_home: pathlib.Path,
    ) -> tuple[tuple[str, ...], dict[str, str], pathlib.Path]:
        config_directory = home / ".config" / "fish"
        config_directory.mkdir(parents=True)
        (config_directory / "config.fish").write_text(
            "set -g fish_history override\nset -e fish_private_mode\n"
        )
        return (
            (),
            {"XDG_CONFIG_HOME": str(home / ".config")},
            data_home / "fish" / "override_history",
        )

    disk = _exercise_shell_startup_override(
        binary="fish",
        configure=_configure,
        sentinel=sentinel,
        mcp_server=mcp_server,
        mcp_pane=mcp_pane,
        tmp_path=tmp_path,
    )

    assert disk is not None
    assert sentinel in disk
