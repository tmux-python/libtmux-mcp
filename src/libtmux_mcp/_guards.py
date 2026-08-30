"""Preconditions a tool refuses on, before tmux is reached.

Each guard names the argument and the consequence, so a caller learns
what to send instead rather than reading a tmux parse error.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shlex
import shutil
import typing as t

from libtmux import exc

if t.TYPE_CHECKING:
    from libtmux.pane import Pane


from libtmux_mcp._errors import ExpectedToolError

logger = logging.getLogger(__name__)


#: POSIX portable environment variable name.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _raise_if_untargeted(tool: str, **targets: str | None) -> None:
    """Refuse a call that delivers input without saying where.

    Reads may default; a tool that types into a pane may not. The
    default was the first LISTED object, which tmux orders by name, so
    ``rename_session`` moved where an untargeted ``send_keys`` landed --
    keystrokes into a pane belonging to a session the caller had never
    touched. Keying the default on the tmux id makes it stable, but
    stable is not the same as correct: nothing about the call says
    which pane was meant.

    The precedent is in this same server. ``kill_window`` requires
    ``window_id``, so the destructive tools already refuse to guess.
    There is no principled reason ``send_keys`` gets to, and it is the
    one that executes something.

    The destination is disclosed in the result today, which is not the
    same as a guard: it arrives after the keystrokes have landed.
    """
    if any(value is not None for value in targets.values()):
        return
    msg = (
        f"{tool} requires an explicit target: pass "
        f"{', '.join(sorted(targets))}. It delivers input to a pane, so "
        "there is no safe default -- the pane it would have picked "
        "belongs to whichever session is oldest, which is unrelated to "
        "what the call is for. Use list_panes or search_panes to find "
        "the pane, and snapshot_pane to confirm what it is running."
    )
    raise ExpectedToolError(msg)


def _raise_if_flag_like(label: str, value: str) -> None:
    """Refuse a caller string tmux would parse as a flag.

    tmux reads flags before quoting can protect anything, and libtmux
    emits ``[name, value]`` with no ``--`` terminator. So a leading
    ``-`` substitutes one command for another silently: measured,
    ``set_environment(name="-u", value="VICTIM")`` UNSET ``VICTIM`` and
    reported ``status="set"``, and ``set_option(option="-g", value="x")``
    turned off ``xterm-keys`` because tmux prefix-matched ``x``.
    """
    if value.startswith("-"):
        msg = (
            f"{label} may not begin with '-': tmux parses it as a flag, so "
            f"the call would run a different command than the one requested "
            f"(got {value!r})."
        )
        raise ExpectedToolError(msg)


def _raise_if_not_env_name(name: str) -> None:
    """Refuse an environment variable name tmux or POSIX cannot hold."""
    if not _ENV_NAME_RE.match(name):
        msg = (
            f"Environment variable name must match [A-Za-z_][A-Za-z0-9_]* "
            f"(got {name!r}). tmux stores anything else verbatim as an "
            "unusable name, and a leading '-' is read as a flag."
        )
        raise ExpectedToolError(msg)


#: Characters that make a spawn command a shell PROGRAM rather than a bare
#: invocation. tmux hands a one-argument command to ``$SHELL -c``
#: (``spawn.c``: "If one argument, pass it to $SHELL -c"), so anything sh
#: interprets is beyond a pre-flight's reach.
_SHELL_METACHARACTERS = frozenset(";&|<>()$`\\\"'\n\t*?[]{}~#=!")

#: Words that legitimately begin a command and are never found on PATH.
#: Without these the pre-flight refuses ``exec sleep 60`` and ``cd /tmp``,
#: which sh runs perfectly well.
_SHELL_BUILTINS = frozenset(
    (
        ".",
        ":",
        "alias",
        "bg",
        "break",
        "case",
        "cd",
        "command",
        "continue",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "fc",
        "fg",
        "fi",
        "for",
        "function",
        "getopts",
        "hash",
        "if",
        "in",
        "jobs",
        "kill",
        "local",
        "newgrp",
        "pwd",
        "read",
        "readonly",
        "return",
        "select",
        "set",
        "shift",
        "source",
        "test",
        "then",
        "time",
        "times",
        "trap",
        "true",
        "type",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "until",
        "wait",
        "while",
    )
)


def _unrunnable_spawn_program(shell: str) -> str | None:
    """Return the program tmux certainly cannot run, else ``None``.

    ``None`` covers both "this will run" and "no pre-flight can tell",
    and the two are deliberately not distinguished: the only safe
    refusal is one that cannot be wrong.

    Anything sh interprets is undecidable, because tmux passes a
    one-argument command to ``$SHELL -c`` rather than exec'ing it.
    Measured: ``cd /tmp && sleep 60``, ``VAR=1 sleep 60`` and
    ``exec sleep 60`` all run, and an earlier version of this check
    refused all three while asserting the pane would die.
    """
    if _SHELL_METACHARACTERS & set(shell):
        return None
    try:
        program = shlex.split(shell)[0]
    except (ValueError, IndexError):
        return None
    if program in _SHELL_BUILTINS:
        return None
    if "/" in program:
        return None if os.access(program, os.X_OK) else program
    return None if shutil.which(program) is not None else program


def _raise_if_shell_unrunnable(shell: str | None, *, consequence: str) -> None:
    """Refuse a spawn command whose program cannot be executed.

    Checked BEFORE spawning because the failure is destructive rather
    than merely wrong: tmux reports success, the new process dies, and
    the pane goes with it. Catching it afterwards can only report the
    loss, and even that races the doomed process.
    """
    if not shell:
        return
    program = _unrunnable_spawn_program(shell)
    if program is None:
        return
    msg = f"{program!r} is not an executable command. {consequence}"
    raise ExpectedToolError(msg)


def _raise_if_start_directory_unusable(start_directory: str | None) -> None:
    """Refuse a start directory the spawned pane could not actually use.

    tmux never reports this. ``spawn.c`` tries ``chdir(cwd)``, then
    ``chdir($HOME)``, then ``chdir("/")``, and succeeds either way -- so
    a typo, a flag-shaped value or an unexpanded ``~`` puts the pane in
    the home directory while the caller is told otherwise. Measured on
    ``create_session``, ``split_window`` and ``create_window``: six
    unusable values, zero errors, every pane in ``$HOME``.

    ``None`` means "not specified" and inherits normally. An empty
    string does not: tmux then takes the client's cwd, which is the MCP
    server's own working directory and has nothing to do with the
    caller.
    """
    if start_directory is None:
        return
    if (
        start_directory
        and pathlib.Path(start_directory).is_dir()
        and os.access(start_directory, os.X_OK)
    ):
        return
    expanded = str(pathlib.Path(start_directory).expanduser())
    if expanded != start_directory and pathlib.Path(expanded).is_dir():
        hint = f" tmux does not expand '~' -- pass {expanded!r}."
    elif not start_directory:
        hint = (
            " An empty string is not the same as omitting the argument: "
            "tmux would use the MCP server's own working directory."
        )
    else:
        hint = ""
    msg = (
        f"start_directory {start_directory!r} is not a usable directory. "
        f"tmux reports no error for this -- it falls back to $HOME, then "
        f"to '/', so the pane would start somewhere that was never "
        f"requested.{hint}"
    )
    raise ExpectedToolError(msg)


def _raise_spawned_pane_gone(shell: str | None) -> t.NoReturn:
    """Report a spawn that tmux accepted and then had nothing to show for."""
    detail = f" running {shell!r}" if shell else ""
    msg = (
        f"The new pane{detail} exited immediately and tmux removed it, so "
        "there is no pane to return. tmux reports a split like this as "
        "successful."
    )
    raise ExpectedToolError(msg) from None


def _raise_if_spawned_pane_is_gone(pane: Pane, shell: str | None) -> None:
    """Refuse to report a pane the spawn has already destroyed.

    The pre-flight cannot cover this on its own: anything sh interprets
    is undecidable in advance, and ``#{session_name}`` reaches sh as a
    comment, so the pane exits 0 and disappears. tmux still reports
    success. Measured: ``refresh()`` raises at t+0, so the vanished pane
    is observable immediately rather than racily.
    """
    try:
        pane.refresh()
    except exc.TmuxObjectDoesNotExist:
        _raise_spawned_pane_gone(shell)
