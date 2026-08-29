"""I/O tools for tmux panes: send keys, capture output, paste, and clear."""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
import re
import shlex
import subprocess
import tempfile
import time
import typing as t
import uuid

from fastmcp.exceptions import ToolError

from libtmux_mcp._tmux_proc import _run_tmux_bounded
from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _map_exception_to_tool_error,
    _raise_if_untargeted,
    _resolve_pane,
    _tmux_argv,
    handle_tool_errors,
    handle_tool_errors_async,
)
from libtmux_mcp._wait_policy import _wait_ceiling_seconds
from libtmux_mcp.models import (
    RunCommandResult,
    SendKeysBatchResult,
    SendKeysOperation,
    SendKeysOperationResult,
)
from libtmux_mcp.tools.pane_tools.state import _read_pane_state

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server


def _batch_timeout_error(timeout: float) -> str:
    """Return the standard send_keys_batch timeout error."""
    return f"batch execution exceeded timeout of {timeout}s"


def _remaining_timeout(deadline: float, timeout: float) -> float:
    """Return the remaining operation budget or raise timeout."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExpectedToolError(_batch_timeout_error(timeout))
    return remaining


#: Bound on a single untimed ``send-keys``. libtmux runs tmux through
#: ``Popen.communicate()`` with no timeout, so an unresponsive server
#: would wedge the tool call. Mirrors ``wait.py``'s per-call ceiling.
_SEND_KEYS_TIMEOUT_SECONDS = 5.0

#: How long to give a shell to acknowledge that it began the payload,
#: as a fraction of the caller's own budget with a floor. Paid only
#: when nothing starts, so it is refusal latency rather than a cost on
#: the happy path: a REPL is reported in half the budget instead of at
#: the end of it.
#:
#: Scaled rather than fixed because over-refusal is the dangerous
#: direction. A shell round trip is 56 ms clean and 714 ms on a
#: configured zsh, but a fixed 3 s refused a LEGITIMATE command under
#: parallel test load at loadavg 31 -- and telling a caller its command
#: did not run, when it is merely slow and about to, is the same
#: double-execution hazard the started channel exists to prevent. The
#: caller's timeout is the only statement of how long the work may
#: honestly take, so the grace is derived from it rather than from a
#: guess about machine speed.
_STARTED_GRACE_FRACTION = 0.5
_STARTED_GRACE_FLOOR_SECONDS = 5.0

#: Shared recovery hint for a pane that cannot accept a shell command.
_BUSY_PANE_SUGGESTION = (
    "Use send_keys for raw input to a program that owns the pane, wait "
    "for the running command to finish, or exit it first. snapshot_pane "
    "reports alternate_on and pane_current_command if you need to check."
)


def _send_keys_argvs(
    pane: Pane,
    keys: str,
    *,
    enter: bool,
    literal: bool,
    suppress_history: bool,
) -> list[list[str]]:
    """Build the ``tmux send-keys`` argv(s) for one send.

    ``--`` terminates flag parsing. Without it tmux reads a payload
    beginning with ``-`` as flags and rejects the command, so `--help`,
    a negative number, or a pasted diff line never reaches the pane.
    ``Pane.send_keys`` omits it and discards tmux's result, which is why
    that failure arrived as a success.

    Enter is a separate call without ``-l`` so it stays a key name
    rather than the literal text ``Enter``.
    """
    pane_id = pane.pane_id
    if pane_id is None:
        msg = "resolved pane has no pane_id"
        raise ExpectedToolError(msg)

    tmux_args = ["send-keys", "-t", pane_id]
    if literal:
        tmux_args.append("-l")
    tmux_args.extend(("--", (" " if suppress_history else "") + keys))

    argvs = [_tmux_argv(pane.server, *tmux_args)]
    if enter:
        argvs.append(_tmux_argv(pane.server, "send-keys", "-t", pane_id, "Enter"))
    return argvs


def _raise_send_keys_error(exc: subprocess.CalledProcessError) -> t.NoReturn:
    """Re-raise a failed ``send-keys`` carrying tmux's own stderr."""
    stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else ""
    msg = f"send-keys failed: {stderr or exc}"
    raise ExpectedToolError(msg) from exc


#: Programs that take over a pane's keyboard and for which typing a
#: shell wrapper is actively destructive -- a pager consumes it as
#: commands, an editor puts it in the buffer where ``:``-prefixed
#: fragments write files. Only ones that can own the pane while
#: ``alternate_on`` is still 0 need naming here; anything that enters
#: the alternate screen is already caught by the flag. Measured:
#: ``top`` repaints the primary screen and needs an entry, while
#: ``htop`` and ``watch`` reach the alternate screen and do not.
#:
#: A DENY-list on purpose. The general signal -- "the foreground
#: command is not the process tmux started" -- was measured and
#: rejected: it refuses a pane where the user simply ran ``bash``
#: inside a ``zsh`` pane, and equally ``sudo -s``, ``ssh`` or
#: ``nix-shell``, all of which have a perfectly good prompt. There is
#: no reliable way to ask tmux "is there a prompt", so this errs
#: toward letting calls through and names only what is known harmful.
_PANE_OWNING_PROGRAMS: frozenset[str] = frozenset(
    {
        "emacs",
        "less",
        "man",
        "more",
        "most",
        "nano",
        "nvim",
        "pico",
        "top",
        "vi",
        "view",
        "vim",
    }
)


def _raise_if_pane_is_busy(pane: Pane) -> None:
    """Refuse when a program, not a shell, owns the pane's keyboard.

    This tool means "run a shell command and report its exit status",
    which needs a shell at a prompt. When a pager or editor owns the
    pane the exit-status wrapper is consumed as ITS keystrokes:
    measured against ``less``, ``s=$?...`` became its save-to-file
    command and a fragment escaped to a shell; in ``vi`` the same
    payload lands in the buffer, where ``:``-prefixed fragments write
    files.

    ``alternate_on`` is the primary signal but is necessary rather than
    sufficient, and the counterexample is reachable through this
    server's own tooling: ``less`` viewing a ``pipe_pane`` capture
    decides the file is binary and prompts "may be a binary file. See
    it anyway?" BEFORE entering the alternate screen, so it owns the
    keyboard with ``alternate_on=0``. Hence the small deny-list above.

    Incomplete by construction: a program not named there, and not yet
    on the alternate screen, still gets the wrapper typed into it. That
    is the deliberate trade -- see ``_PANE_OWNING_PROGRAMS`` for why a
    general test was rejected.
    """
    occupant = _read_pane_current_command(pane)
    state = _read_pane_state(pane)
    # Copy/view/clock mode owns the keyboard while alternate_on stays 0
    # and pane_current_command still reads as the shell, so both other
    # arms miss it. Measured with a client attached: the payload was
    # consumed as copy-mode keystrokes, the command never ran, and the
    # user's scroll position was destroyed in 7 of 8 trials -- while
    # the result claimed command_may_still_run.
    if state.in_mode:
        msg = (
            f"pane {pane.pane_id} is in a tmux mode (copy, view or clock), "
            "so keys go to that mode rather than to a shell. Exit it with "
            "exit_copy_mode first."
        )
        raise ExpectedToolError(msg, suggestion=_BUSY_PANE_SUGGESTION)

    if state.alternate_on:
        named = f" ({occupant})" if occupant else ""
        msg = (
            f"pane {pane.pane_id} is running a full-screen program{named}, "
            "so it has no shell prompt to accept a command. Sending one "
            "would type this tool's exit-status wrapper into that program."
        )
        raise ExpectedToolError(msg, suggestion=_BUSY_PANE_SUGGESTION)

    if occupant is not None and occupant.lstrip("-") in _PANE_OWNING_PROGRAMS:
        msg = (
            f"pane {pane.pane_id} is running {occupant!r}, which owns the "
            "keyboard, so it has no shell prompt to accept a command. "
            "Sending one would type this tool's exit-status wrapper into "
            "that program."
        )
        raise ExpectedToolError(msg, suggestion=_BUSY_PANE_SUGGESTION)


def _read_pane_current_command(pane: Pane) -> str | None:
    """Return the pane's foreground command, or None if tmux won't say."""
    stdout = pane.display_message("#{pane_current_command}", get_text=True)
    return stdout[0] if stdout and stdout[0] else None


def _run_send_keys_argv(argv: list[str]) -> None:
    """Run one ``tmux send-keys`` argv under the untimed ceiling."""
    try:
        subprocess.run(
            argv,
            check=True,
            capture_output=True,
            timeout=_SEND_KEYS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        msg = f"send-keys timed out after {_SEND_KEYS_TIMEOUT_SECONDS}s"
        raise ExpectedToolError(msg) from e
    except subprocess.CalledProcessError as e:
        _raise_send_keys_error(e)


def _run_send_keys(
    pane: Pane,
    keys: str,
    *,
    enter: bool,
    literal: bool,
    suppress_history: bool,
) -> None:
    """Send keys to *pane*, raising if tmux rejected them."""
    for argv in _send_keys_argvs(
        pane,
        keys,
        enter=enter,
        literal=literal,
        suppress_history=suppress_history,
    ):
        _run_send_keys_argv(argv)


def _run_timed_send_keys_argv(
    argv: list[str],
    *,
    deadline: float,
    timeout: float,
) -> None:
    """Run one ``tmux send-keys`` argv within the batch deadline."""
    try:
        subprocess.run(
            argv,
            check=True,
            capture_output=True,
            timeout=_remaining_timeout(deadline, timeout),
        )
    except subprocess.TimeoutExpired as e:
        raise ExpectedToolError(_batch_timeout_error(timeout)) from e
    except subprocess.CalledProcessError as e:
        _raise_send_keys_error(e)


def _run_timed_send_keys(
    pane: Pane,
    operation: SendKeysOperation,
    *,
    deadline: float,
    timeout: float,
) -> None:
    """Run ``tmux send-keys`` for one operation within the batch deadline."""
    for argv in _send_keys_argvs(
        pane,
        operation.keys,
        enter=operation.enter,
        literal=operation.literal,
        suppress_history=operation.suppress_history,
    ):
        _run_timed_send_keys_argv(argv, deadline=deadline, timeout=timeout)


@handle_tool_errors
def send_keys(
    keys: str,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    enter: bool = True,
    literal: bool = False,
    suppress_history: bool = False,
    socket_name: str | None = None,
) -> str:
    """Send keys (commands or text) to a tmux pane.

    Use this for raw interactive input: TUI keys, control sequences,
    partial shell input, or persistent shell state. Use ``send_keys_batch``
    when you need several ordered raw-input operations.

    For authored shell commands that need completion, exit status, or
    captured output, use ``run_command`` instead. For custom completion
    outside that shape, compose ``tmux wait-for -S <channel>`` into the
    shell command and call ``wait_for_channel``. For repeated observation
    after input, prefer ``capture_since``; reserve ``wait_for_text``
    for output the agent does not author.

    Do NOT call ``capture_pane`` immediately — both the read and the
    pattern-match paths race the pane's PTY draw.

    **Size limit:** tmux rejects a ``send-keys`` argument beyond roughly
    16 KB with ``command too long``. ``paste_text`` routes through a
    buffer instead of argv and takes far more, so use it for large
    payloads.

    **Verifying a write:** do not string-compare captured text against
    what you sent. The bytes arrive correctly and can still read back
    different, because the pane's LINE EDITOR echoes them, not tmux.
    zsh renders a character it considers unprintable as ``<XXXX>``:
    measured, ``e`` + U+0301 echoes as ``e<0301>``, U+200D as
    ``<200d>``, while precomposed U+00E9 survives -- so ``école``
    round-trips composed and does not decomposed. The same payload
    reaches a pane running ``sh`` or ``cat`` verbatim, which is why
    this looks unreproducible until the occupant is taken into account.
    tmux itself has no such transform. Compare on an ASCII marker you
    control instead.

    Parameters
    ----------
    keys : str
        The keys or text to send.
    pane_id : str
        Pane ID (e.g. '%1'). One of pane_id / session_id / session_name /
        window_id is REQUIRED: this tool delivers input, so it will not
        pick a pane for you. ``list_panes`` finds one, and
        ``create_session`` / ``create_window`` / ``split_window`` return
        the new pane's id directly.
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    enter : bool
        Whether to press Enter after sending keys. Default True.
    literal : bool
        Whether to send keys literally (no tmux interpretation). Default False.
    suppress_history : bool
        Suppress shell history by prepending a space; only effective where
        the shell ignores space-prefixed commands. Default False.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    _raise_if_untargeted(
        "send_keys",
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    _run_send_keys(
        pane,
        keys,
        enter=enter,
        literal=literal,
        suppress_history=suppress_history,
    )
    return f"Keys sent to pane {pane.pane_id}"


@handle_tool_errors
def send_keys_batch(
    operations: list[SendKeysOperation],
    on_error: t.Literal["stop", "continue"] = "stop",
    timeout: float | None = None,
    socket_name: str | None = None,
) -> SendKeysBatchResult:
    """Send an ordered batch of raw key/text operations to tmux panes.

    Use this for bulk TUI or persistent-shell input where each item is the
    same kind of low-level terminal interaction as
    :func:`~libtmux_mcp.tools.pane_tools.send_keys`. For authored shell
    commands that need exit status and captured output, use
    :func:`~libtmux_mcp.tools.pane_tools.run_command` instead. For
    repeated observation after sending input, use
    :func:`~libtmux_mcp.tools.pane_tools.capture_since` with its returned
    cursor.

    This tool intentionally does not compose heterogeneous operations such
    as send → wait → capture. Keeping the batch homogeneous preserves clear
    per-operation error attribution and avoids embedding a workflow DSL in
    the MCP tool surface.

    Parameters
    ----------
    operations : list of SendKeysOperation
        Ordered raw-input operations to send. Each one must name its own
        target -- pane_id / session_id / session_name / window_id -- and
        is refused individually if it does not, so one untargeted entry
        among targeted ones fails alone rather than being sent somewhere
        arbitrary.
    on_error : {"stop", "continue"}
        Whether to stop at the first failed operation or keep attempting
        later operations. Default "stop".
    timeout : float, optional
        Maximum time in seconds to allow the batch to run before aborting.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    SendKeysBatchResult
        Per-operation results with success/error counts and stop index.
    """
    if not operations:
        msg = "operations must not be empty"
        raise ExpectedToolError(msg)
    if on_error not in {"stop", "continue"}:
        msg = "on_error must be 'stop' or 'continue'"
        raise ExpectedToolError(msg)
    server = _get_server(socket_name=socket_name)
    results: list[SendKeysOperationResult] = []
    stopped_at: int | None = None
    batch_started = time.monotonic()
    deadline = batch_started + timeout if timeout is not None else None

    for index, operation in enumerate(operations):
        if deadline is not None and time.monotonic() > deadline:
            assert timeout is not None
            results.append(
                SendKeysOperationResult(
                    index=index,
                    pane_id=operation.pane_id,
                    success=False,
                    error=_batch_timeout_error(timeout),
                    elapsed_seconds=0.0,
                )
            )
            if on_error == "stop":
                stopped_at = index
                break
            continue

        started = time.monotonic()
        pane_id: str | None = None
        try:
            # Per operation, not once for the batch: a batch is a list of
            # independent sends, and one untargeted entry among targeted
            # ones is the case a whole-batch check would miss.
            _raise_if_untargeted(
                f"send_keys_batch operation {index}",
                pane_id=operation.pane_id,
                session_name=operation.session_name,
                session_id=operation.session_id,
                window_id=operation.window_id,
            )
            pane = _resolve_pane(
                server,
                pane_id=operation.pane_id,
                session_name=operation.session_name,
                session_id=operation.session_id,
                window_id=operation.window_id,
            )
            pane_id = pane.pane_id
            if pane_id is None:
                results.append(
                    SendKeysOperationResult(
                        index=index,
                        pane_id=None,
                        success=False,
                        error="resolved pane has no pane_id",
                        elapsed_seconds=time.monotonic() - started,
                    )
                )
                if on_error == "stop":
                    stopped_at = index
                    break
                continue
            if deadline is None:
                _run_send_keys(
                    pane,
                    operation.keys,
                    enter=operation.enter,
                    literal=operation.literal,
                    suppress_history=operation.suppress_history,
                )
            else:
                assert timeout is not None
                _run_timed_send_keys(
                    pane,
                    operation,
                    deadline=deadline,
                    timeout=timeout,
                )
        except Exception as e:
            elapsed = time.monotonic() - started
            tool_err = (
                e
                if isinstance(e, ToolError)
                else _map_exception_to_tool_error("send_keys_batch", e)
            )
            error = str(tool_err)
            suggestion = getattr(tool_err, "suggestion", None)
            if suggestion:
                error = f"{error}\n{suggestion}"
            results.append(
                SendKeysOperationResult(
                    index=index,
                    pane_id=pane_id,
                    success=False,
                    error=error,
                    elapsed_seconds=elapsed,
                )
            )
            if on_error == "stop":
                stopped_at = index
                break
            continue

        results.append(
            SendKeysOperationResult(
                index=index,
                pane_id=pane_id,
                success=True,
                elapsed_seconds=time.monotonic() - started,
            )
        )

    succeeded = sum(result.success for result in results)
    failed = len(results) - succeeded
    return SendKeysBatchResult(
        results=results,
        succeeded=succeeded,
        failed=failed,
        stopped_at=stopped_at,
    )


@handle_tool_errors_async
async def run_command(
    command: str,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    timeout: float = 30.0,
    max_lines: int | None = None,
    suppress_history: bool = False,
    socket_name: str | None = None,
) -> RunCommandResult:
    """Run a shell command in a pane, wait for completion, and capture output.

    Use for the common terminal workflow: run this command, wait until it
    completes, then report whether it succeeded. The command is sent to
    the pane's interactive shell, followed by a private ``tmux wait-for``
    signal and a private pane option carrying the shell exit status. This
    is the AUTHORED-output path — the command you pass is what the wait
    synchronizes on. Reserve ``wait_for_text`` for output you did not
    author: another process, a human, or a background job.

    Because it runs in the pane's INTERACTIVE shell, every call pays
    that shell's per-command hooks. Measured on one machine: 914 ms
    against a configured zsh whose prompt runs ``git status``, versus
    71 ms against ``zsh -f`` and 64 ms against ``sh`` — about 615 ms of
    it is the shell, not this tool. For throughput-sensitive scripted
    work, target a pane running a minimal shell.

    The command runs in a subshell, so ``cd``, ``export`` and other shell
    state changes do not persist to later calls.

    **Requires a shell at a prompt.** A pane running a full-screen
    program (``less``, ``vi``, ``htop``) owns the keyboard, so this
    tool's exit-status wrapper would be typed into THAT program rather
    than run; such a call is refused. Use ``send_keys`` for raw input to
    a full-screen program.

    **A timeout does not cancel the command.** The keystrokes are
    already in the pane's input buffer, so a shell that is busy now runs
    them whenever it next reads a line — possibly long after this
    returns. ``command_may_still_run`` reports that. Do not retry a
    non-idempotent command on a timed-out result without checking the
    pane first.

    Parameters
    ----------
    command : str
        Shell command to run in the target pane. Single-line only: join
        with ``'; '``, or use ``send_keys``/``paste_text`` for raw
        multi-line input.
    pane_id : str
        Pane ID (e.g. '%1'). One of pane_id / session_id / session_name /
        window_id is REQUIRED: this tool delivers input, so it will not
        pick a pane for you. ``list_panes`` finds one, and
        ``create_session`` / ``create_window`` / ``split_window`` return
        the new pane's id directly.
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    timeout : float
        Maximum seconds to wait for command completion. Capped by the
        same server wait ceiling as ``wait_for_text``; an over-large
        value is not an error — the wait returns at the ceiling and
        the timeout actually enforced is reported on
        ``RunCommandResult.effective_timeout``.
    max_lines : int or None
        Maximum pane output lines to return. Defaults to all captured
        visible output; pass a small value for a tail-only summary.
    suppress_history : bool
        For MCP calls, omission uses the server's LIBTMUX_SUPPRESS_HISTORY
        default; an explicit value overrides it. Direct Python calls default
        to False. Best effort: the shell must honor space-prefixed history
        suppression.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    RunCommandResult
        Typed command result with exit status, timeout state, and
        tail-preserved pane output.
    """
    if not command.strip():
        msg = "command must not be empty"
        raise ExpectedToolError(msg)
    if "\n" in command or "\r" in command:
        # The wrapper must be ONE line, because that is what makes its
        # "did this start" answer true. A shell mid-`read` consumes a
        # whole line as its answer: one line is eaten entire and nothing
        # executes, while a split wrapper has its first line eaten and
        # then RUNS the rest -- and the tool would report "it has not
        # run" about a command that just did, talking the caller into a
        # retry that executes it twice. Measured.
        msg = (
            "command must be a single line. A multi-line command cannot be "
            "sent atomically, so this tool cannot tell whether a pane "
            "swallowed it or ran it. Join the lines with '; ', or use "
            "send_keys / paste_text if you need raw multi-line input."
        )
        raise ExpectedToolError(msg)

    # After the multiline refusal, whose ordering ahead of any tmux
    # contact is deliberate and asserted: that check keeps a breakout
    # payload away from tmux entirely.
    _raise_if_untargeted(
        "run_command",
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    if timeout <= 0:
        msg = "timeout must be positive"
        raise ExpectedToolError(msg)
    effective_timeout = min(timeout, _wait_ceiling_seconds())

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    # A full-screen program (less, vi, htop) owns the pane's keyboard,
    # so the wrapper below is consumed as ITS keystrokes rather than by
    # a shell: measured against `less`, `s=$?...` became less's
    # save-to-file command and a fragment escaped to a shell. In `vi`
    # the same payload lands in the buffer, where `:`-prefixed
    # fragments are commands that edit and write files. This tool means
    # "run a shell command and report its exit status", which requires
    # a shell, so refuse rather than type into whatever is there.
    await asyncio.to_thread(_raise_if_pane_is_busy, pane)

    command_id = uuid.uuid4().hex[:10]
    channel = f"r_{command_id}"
    status_option = f"@s_{command_id}"
    target_pane_id = pane.pane_id
    if target_pane_id is None:
        msg = "resolved pane has no pane_id"
        raise ExpectedToolError(msg)
    status_cmd = shlex.join(
        _tmux_argv(server, "set-option", "-p", "-t", target_pane_id, status_option)
    )
    signal_cmd = shlex.join(_tmux_argv(server, "wait-for", "-S", channel))
    started_channel = f"p_{command_id}"
    started_cmd = shlex.join(_tmux_argv(server, "wait-for", "-S", started_channel))
    history_prefix = " " if suppress_history else ""
    # ``started_cmd`` runs before the command and answers a question the
    # completion channel cannot: did a shell execute any of this at all?
    # Without it a swallowed payload and a slow command are the same
    # observation, and the tool reported the slow one -- "may still run"
    # about something that never ran.
    #
    # Always one line -- multi-line commands are refused above, because
    # atomicity is what makes the "did this start" answer true.
    payload = (
        f"{history_prefix}{started_cmd}; ( {command.strip()} ); "
        f's=$?; {status_cmd} "$s"; {signal_cmd}'
    )

    # Read before sending, so a later change is evidence the pane
    # accepted the line rather than a difference that predates it.
    entry_occupant = await asyncio.to_thread(_read_pane_current_command, pane)

    started = time.monotonic()
    await asyncio.to_thread(
        _run_send_keys,
        pane,
        payload,
        enter=True,
        literal=True,
        suppress_history=False,
    )

    # Did a shell begin the payload at all? Bounded by a grace rather
    # than the caller's budget: the answer does not get truer by waiting
    # longer, and paying the full timeout for it made the failure path
    # fifty times slower than the success path.
    grace = max(
        _STARTED_GRACE_FLOOR_SECONDS, effective_timeout * _STARTED_GRACE_FRACTION
    )
    # Only a caller who gave us MORE than the grace has told us enough
    # to refuse. When the budget is at or below it, "has not started
    # yet" and "never will" are the same observation, so that stays a
    # plain timeout.
    can_refuse = grace < effective_timeout
    started_ok = await _channel_already_signalled(
        server, started_channel, timeout=min(grace, effective_timeout)
    )
    if not started_ok and can_refuse:
        occupant = await asyncio.to_thread(_read_pane_current_command, pane)
        # A foreground process that CHANGED since the send is positive
        # evidence a shell read the line and is working -- slow, not
        # wedged. Extend rather than refuse: measured, a prompt hook
        # sleeping 8 s is refused by the grace alone while the command
        # runs, which is the double execution this guard exists to
        # prevent, arriving on an ordinary call.
        #
        # Partial by construction: slow work in a shell BUILTIN spawns
        # no child, so it still reads as unchanged. It fails toward
        # refusing, which is the behaviour without it, so it can only
        # remove over-refusals and never create a false accept.
        started_ok = occupant != entry_occupant
    if not started_ok and can_refuse:
        named = f" (foreground: {occupant!r})" if occupant else ""
        # Deliberately does not guess between the two readings. A REPL
        # and a still-running command look identical from here, and
        # they call for opposite reactions -- one is safe to retry and
        # the other would run the command twice.
        msg = (
            f"pane {target_pane_id} never reached a shell prompt for this "
            f"command{named}, so it has not run. If the pane is mid-"
            "continuation, in a `read`, or in a REPL, it never will. If that "
            "process is still running, the input is queued behind it and "
            "will run when it exits. Check capture_pane before retrying."
        )
        raise ExpectedToolError(msg, suggestion=_BUSY_PANE_SUGGESTION)

    timed_out = not started_ok
    wait_argv = _tmux_argv(server, "wait-for", channel)
    # The wait must be owned by a killable child, not a worker thread.
    # ``asyncio.to_thread(subprocess.run, ...)`` cannot be interrupted:
    # cancelling the call raised ``CancelledError`` at once while the
    # thread stayed blocked in an untimed ``waitpid``, so ``tmux
    # wait-for`` ran on for the rest of the budget — measured at 22 s
    # of orphan for a 25 s ``run_command`` cancelled at 3 s. This is
    # the most-cancelled wait of the three: agents routinely bail out
    # of a long shell command. ``_run_tmux_bounded`` kills the child on
    # expiry and on cancellation alike.
    returncode = 0
    stderr_bytes = b""
    if started_ok:
        try:
            returncode, _stdout, stderr_bytes = await _run_tmux_bounded(
                wait_argv,
                timeout=max(effective_timeout - (time.monotonic() - started), 0.0),
            )
        except TimeoutError:
            timed_out = True
    if returncode != 0:
        stderr = stderr_bytes.decode(errors="replace").strip()
        detail = stderr or f"exit {returncode}"
        msg = f"wait-for failed for run_command channel {channel!r}: {detail}"
        raise ExpectedToolError(msg)

    elapsed = time.monotonic() - started
    exit_status: int | None = None
    if not timed_out:
        status = pane.cmd("show-option", "-p", "-v", status_option).stdout
        status_text = status[0].strip() if status else ""
        try:
            exit_status = int(status_text)
        except ValueError as e:
            msg = f"run_command could not read exit status from {status_option!r}"
            raise ExpectedToolError(msg) from e
        with contextlib.suppress(Exception):
            pane.cmd("set-option", "-p", "-u", status_option)

    # join_wrapped keeps the per-call markers on one logical row so the
    # filter's exact-marker match survives a wide prompt; it also strips
    # sync fragments that still wrap across rows.
    raw_lines = await asyncio.to_thread(pane.capture_pane, join_wrapped=True)
    visible_lines = _filter_run_command_internal_lines(
        raw_lines,
        channel=channel,
        status_option=status_option,
        started_channel=started_channel,
    )
    kept_lines, truncated, dropped = _truncate_lines_tail(visible_lines, max_lines)
    return RunCommandResult(
        pane_id=target_pane_id,
        exit_status=exit_status,
        timed_out=timed_out,
        command_may_still_run=timed_out,
        elapsed_seconds=elapsed,
        output=kept_lines,
        output_truncated=truncated,
        output_truncated_lines=dropped,
        effective_timeout=effective_timeout,
    )


#: Default line cap applied to :func:`capture_pane` and similar scrollback
#: readers. Large enough to cover typical prompt + a few screens of output,
#: small enough that a pathological pane (e.g. 50K lines of ``tail -f``)
#: cannot blow the agent's context window on a single call. Callers who
#: need a full capture can pass ``max_lines=None`` to opt out.
CAPTURE_DEFAULT_MAX_LINES = 500


def _truncate_lines_tail(
    lines: list[str], max_lines: int | None
) -> tuple[list[str], bool, int]:
    """Return the tail of ``lines`` at most ``max_lines`` long.

    Tail-preserving truncation is required for terminal output: the
    most recent lines (active prompt, latest command output) live at
    the bottom of the scrollback buffer. Dropping the head keeps what
    the agent actually needs.

    Parameters
    ----------
    lines : list of str
        The captured lines, oldest first.
    max_lines : int or None
        Maximum number of lines to keep. ``None`` disables truncation.

    Returns
    -------
    tuple
        ``(kept, truncated, dropped)`` — the kept suffix, whether
        truncation happened, and how many lines were dropped.

    Examples
    --------
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=2)
    (['b', 'c'], True, 1)
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=5)
    (['a', 'b', 'c'], False, 0)
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=None)
    (['a', 'b', 'c'], False, 0)
    >>> _truncate_lines_tail(["a", "b", "c"], max_lines=0)
    Traceback (most recent call last):
    libtmux_mcp._utils.ExpectedToolError: max_lines must be at least 1, ...
    """
    if max_lines is not None and max_lines < 1:
        # Python slices a non-positive cap into nonsense rather than
        # failing: ``lines[-0:]`` is the WHOLE list, so max_lines=0
        # returned more rows than no truncation at all while announcing
        # that everything had been dropped, and a negative inflated the
        # count past the pane's own size -- 112 truncated from 12.
        # The header is this tool's only disclosure channel, so a number
        # that cannot be true is the whole defect.
        msg = (
            f"max_lines must be at least 1, or null for no limit (received {max_lines})"
        )
        raise ExpectedToolError(msg)
    if max_lines is None or len(lines) <= max_lines:
        return lines, False, 0
    dropped = len(lines) - max_lines
    return lines[-max_lines:], True, dropped


async def _channel_already_signalled(
    server: Server, channel: str, timeout: float
) -> bool:
    """Whether ``channel`` was signalled, without waiting for it.

    tmux latches a ``wait-for -S`` that has no waiter, so a later wait
    on that channel returns at once -- measured at 4 ms against 2 s for
    a channel nobody signalled. That makes this a question about the
    past rather than a second wait, which is why it can sit on the
    timeout path without adding to the budget.
    """
    argv = _tmux_argv(server, "wait-for", channel)
    try:
        returncode, _stdout, _stderr = await _run_tmux_bounded(argv, timeout=timeout)
    except TimeoutError:
        return False
    return returncode == 0


def _filter_run_command_internal_lines(
    lines: list[str], channel: str, status_option: str, started_channel: str = ""
) -> list[str]:
    """Drop private run_command synchronization rows from captured output.

    The current call is matched by exact channel/status markers. Older
    wrapped fragments are matched by private wrapper shape so prior
    scrollback does not leak into output.
    """
    shell_arg = r"(?:'[^']*'|\S+)"
    tmux_prefix = rf"(?:\S*/)?tmux(?:\s+-[LS]\s+{shell_arg})*\s+"
    target_pane_arg = rf"(?:\s+-t\s+{shell_arg})?"
    status_line_re = re.compile(
        r"(?:__libtmux_mcp_status|s)=\$\?;\s*"
        + tmux_prefix
        + r"set-option -p"
        + target_pane_arg
        + r"\s+"
        + r"(?P<prefix>@libtmux_mcp_status_|@s_)"
        + r"(?P<id>[0-9a-fA-F]+)(?![0-9A-Za-z_])"
    )
    wait_line_re = re.compile(
        r'[0-9a-fA-F]*\s*"\$(?:__libtmux_mcp_status|s)";\s*'
        + tmux_prefix
        + r"wait-for -S "
        + r"(?P<prefix>libtmux_mcp_run_|r_)"
        + r"(?P<id>[0-9a-fA-F]*)(?![0-9A-Za-z_])"
    )
    internal_markers = tuple(
        marker for marker in (channel, status_option, started_channel) if marker
    )
    hex_chars = frozenset("0123456789abcdefABCDEF")
    kept: list[str] = []
    drop_hex_continuation = False

    def expected_private_id_length(prefix: str) -> int:
        return 32 if "libtmux_mcp" in prefix else 10

    for line in lines:
        stripped = line.strip()
        if (
            drop_hex_continuation
            and 8 <= len(stripped) <= 32
            and all(char in hex_chars for char in stripped)
        ):
            drop_hex_continuation = False
            continue

        if any(marker in line for marker in internal_markers):
            drop_hex_continuation = False
            continue

        status_match = status_line_re.search(line)
        wait_match = wait_line_re.search(line)
        if status_match or wait_match:
            drop_hex_continuation = False
            for match in (status_match, wait_match):
                if match is None:
                    continue
                private_id = match.group("id")
                expected_len = expected_private_id_length(match.group("prefix"))
                if len(private_id) < expected_len:
                    drop_hex_continuation = True
            continue

        drop_hex_continuation = False
        kept.append(line)
    return kept


@handle_tool_errors
def capture_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    start: int | None = None,
    end: int | None = None,
    max_lines: int | None = CAPTURE_DEFAULT_MAX_LINES,
    socket_name: str | None = None,
) -> str:
    """Capture the visible contents of a tmux pane (terminal scrollback).

    Use for tmux pane output — 'capture the build log', 'what did the
    server print' — not editor file contents. The tool for reading what
    is displayed in a terminal; use search_panes to search across
    multiple panes at once.

    Output is tail-preserved: when the capture exceeds ``max_lines``
    the oldest lines are dropped and the returned string is prefixed
    with a single ``[... truncated K lines ...]`` header line so the
    agent can tell truncation occurred and re-request with a narrower
    ``start``/``end`` window or a larger ``max_lines`` if needed. Pass
    ``max_lines=None`` to disable truncation entirely.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    start : int, optional
        Start line number. 0 is the first visible line. Negative values
        reach into scrollback history (e.g. -100 for last 100 lines).
    end : int, optional
        End line number.
    max_lines : int or None
        Maximum number of lines to return. Defaults to
        ``CAPTURE_DEFAULT_MAX_LINES``. Pass ``None`` to return the
        full capture with no truncation.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Captured pane content as text. When truncated, the first line
        is a ``[... truncated K lines ...]`` marker.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    lines = pane.capture_pane(start=start, end=end)
    kept, truncated, dropped = _truncate_lines_tail(lines, max_lines)
    if truncated:
        return f"[... truncated {dropped} lines ...]\n" + "\n".join(kept)
    return "\n".join(kept)


@handle_tool_errors
def clear_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> str:
    """Clear a pane's screen AND its scrollback history.

    Destroys up to ``history-limit`` lines of the user's terminal
    history, not just the visible screen, and there is no undo. Measured:
    ``history_size`` 132 -> 0, with the prior content unreachable through
    ``capture-pane -S -300`` afterwards. Reach for it only when losing
    that history is intended.

    Use before a fresh run_command call or raw-input observation workflow
    when prior scrollback would make the result harder to inspect.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.reset()
    return f"Pane cleared: {pane.pane_id}"


@handle_tool_errors
def paste_text(
    text: str,
    pane_id: str | None = None,
    bracket: bool = True,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> str:
    """Paste multi-line text into a pane using tmux paste buffers.

    Uses tmux's load-buffer and paste-buffer for clean multi-line input,
    avoiding the issues of sending text line-by-line via send_keys.
    Supports bracketed paste mode for terminals that handle it.

    **When to use this vs. load_buffer + paste_buffer:** ``paste_text``
    is the fire-and-forget path — the buffer is created, pasted, and
    deleted in one call. Use ``load_buffer`` + ``paste_buffer`` when
    you need to stage content first, paste it into multiple panes, or
    inspect it with ``show_buffer`` before pasting.

    Parameters
    ----------
    text : str
        The text to paste.
    pane_id : str
        Pane ID (e.g. '%1'). One of pane_id / session_id / session_name /
        window_id is REQUIRED: this tool delivers input, so it will not
        pick a pane for you. ``list_panes`` finds one, and
        ``create_session`` / ``create_window`` / ``split_window`` return
        the new pane's id directly.
    bracket : bool
        Whether to use bracketed paste mode. Default True.
        Bracketed paste wraps the text in escape sequences that tell
        the terminal "this is pasted text, not typed input".

        **A trailing newline therefore does NOT run the command**: the
        shell holds it in its edit buffer, where it executes when Enter
        next reaches the pane from any source. Pass ``bracket=False``
        to submit, or follow with ``send_keys(keys="Enter")``.
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    _raise_if_untargeted(
        "paste_text",
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    if not text:
        # tmux creates no buffer for empty content, so the follow-up
        # paste-buffer failed with "no buffer libtmux_mcp_..._paste" --
        # an error for a no-op, naming an internal buffer the caller
        # never chose. Pasting nothing succeeds and does nothing.
        return f"Text pasted to pane {pane.pane_id}"

    # Use a unique named tmux buffer so we don't clobber the user's
    # unnamed paste buffer, and so we can reliably clean up on error
    # paths (paste-buffer -b NAME -d deletes the named buffer). The
    # shape matches ``buffer_tools._BUFFER_NAME_RE`` exactly —
    # ``libtmux_mcp_<32-hex>_<logical>`` — so a future operator-facing
    # listing of MCP-owned buffers sees paste-through buffers and
    # ``load_buffer`` buffers uniformly under one regex.
    buffer_name = f"libtmux_mcp_{uuid.uuid4().hex}_paste"
    tmppath: str | None = None
    try:
        # Write text to a temp file and load into tmux buffer
        # (libtmux's cmd() doesn't support stdin).
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmppath = f.name  # bind first so cleanup works even if write fails
            f.write(text)

        load_args = _tmux_argv(server, "load-buffer", "-b", buffer_name, tmppath)

        try:
            subprocess.run(load_args, check=True, capture_output=True, timeout=5.0)
        except subprocess.TimeoutExpired as e:
            msg = f"load-buffer timeout after 5s for {buffer_name!r}"
            raise ExpectedToolError(msg) from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
            msg = f"load-buffer failed: {stderr or e}"
            raise ExpectedToolError(msg) from e

        # Paste from the named buffer. ``delete_after=True`` (``-d``)
        # deletes only that named buffer, leaving any unnamed user
        # buffer intact.
        pane.paste_buffer(buffer_name=buffer_name, bracket=bracket, delete_after=True)
    finally:
        if tmppath is not None:
            pathlib.Path(tmppath).unlink(missing_ok=True)
        # Defensive: the buffer should already be gone (paste-buffer -d
        # deletes it), but if paste-buffer failed before -d took effect
        # we leak an entry in the tmux server. Best-effort delete.
        with contextlib.suppress(Exception):
            server.delete_buffer(buffer_name=buffer_name)

    if bracket and text.endswith(("\n", "\r")):
        # Bracketed paste tells the terminal "this is pasted text, not
        # typed input", so the shell holds the trailing newline in its
        # edit buffer instead of submitting. Correct terminal behavior
        # and a safe default -- but an unqualified "Text pasted" reads
        # as "your command ran", and the text is not inert: it executes
        # the moment ANY Enter reaches this pane, from any source,
        # possibly long after this call and out of order with it.
        return (
            f"Text pasted to pane {pane.pane_id}, but NOT submitted: with "
            "bracket=True the trailing newline goes into the shell's edit "
            "buffer, where it will run whenever Enter next reaches this "
            "pane. Pass bracket=False, or follow with "
            "send_keys(keys='Enter'), to run it now."
        )
    return f"Text pasted to pane {pane.pane_id}"
