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

from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _map_exception_to_tool_error,
    _resolve_pane,
    _tmux_argv,
    handle_tool_errors,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    RunCommandResult,
    SendKeysBatchResult,
    SendKeysOperation,
    SendKeysOperationResult,
)


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
    after input, prefer ``capture_since``; reserve ``wait_for_text`` and
    ``wait_for_content_change`` for output the agent does not author.

    Do NOT call ``capture_pane`` immediately — both the read and the
    pattern-match paths race the pane's PTY draw.

    Parameters
    ----------
    keys : str
        The keys or text to send.
    pane_id : str, optional
        Pane ID (e.g. '%1').
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
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.send_keys(
        keys,
        enter=enter,
        suppress_history=suppress_history,
        literal=literal,
    )
    return f"Keys sent to pane {pane.pane_id}"


@handle_tool_errors
def send_keys_batch(
    operations: list[SendKeysOperation],
    on_error: t.Literal["stop", "continue"] = "stop",
    socket_name: str | None = None,
) -> SendKeysBatchResult:
    """Send an ordered batch of raw key/text operations to tmux panes.

    Use this for bulk TUI or persistent-shell input where each item is the
    same kind of low-level terminal interaction as :func:`send_keys`. For
    authored shell commands that need exit status and captured output, use
    :func:`run_command` instead. For repeated observation after sending input,
    use :func:`capture_since` with its returned cursor.

    This tool intentionally does not compose heterogeneous operations such
    as send → wait → capture. Keeping the batch homogeneous preserves clear
    per-operation error attribution and avoids embedding a workflow DSL in
    the MCP tool surface.

    Parameters
    ----------
    operations : list of SendKeysOperation
        Ordered raw-input operations to send.
    on_error : {"stop", "continue"}
        Whether to stop at the first failed operation or keep attempting
        later operations. Default "stop".
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

    for index, operation in enumerate(operations):
        started = time.monotonic()
        pane_id: str | None = None
        try:
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
            pane.send_keys(
                operation.keys,
                enter=operation.enter,
                suppress_history=operation.suppress_history,
                literal=operation.literal,
            )
        except Exception as e:
            elapsed = time.monotonic() - started
            if isinstance(e, ToolError):
                error = str(e)
            else:
                error = str(_map_exception_to_tool_error("send_keys_batch", e))
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
    signal and a private pane option carrying the shell exit status.

    The command runs in a subshell, so ``cd``, ``export`` and other shell
    state changes do not persist to later calls.

    Parameters
    ----------
    command : str
        Shell command to run in the target pane.
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    timeout : float
        Maximum seconds to wait for command completion.
    max_lines : int or None
        Maximum pane output lines to return. Defaults to all captured
        visible output; pass a small value for a tail-only summary.
    suppress_history : bool
        Suppress shell history by prepending a space; only effective where
        the shell ignores space-prefixed commands. Default False.
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
    if timeout <= 0:
        msg = "timeout must be positive"
        raise ExpectedToolError(msg)

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
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
    history_prefix = " " if suppress_history else ""
    payload = "\n".join(
        (
            f"{history_prefix}(",
            command.rstrip(),
            (f'); s=$?; {status_cmd} "$s"; {signal_cmd}'),
        )
    )

    started = time.monotonic()
    await asyncio.to_thread(pane.send_keys, payload, enter=True, literal=True)

    timed_out = False
    wait_argv = _tmux_argv(server, "wait-for", channel)
    try:
        await asyncio.to_thread(
            subprocess.run,
            wait_argv,
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        msg = f"wait-for failed for run_command channel {channel!r}: {stderr or e}"
        raise ExpectedToolError(msg) from e

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
    )
    kept_lines, truncated, dropped = _truncate_lines_tail(visible_lines, max_lines)
    return RunCommandResult(
        pane_id=target_pane_id,
        exit_status=exit_status,
        timed_out=timed_out,
        elapsed_seconds=elapsed,
        output=kept_lines,
        output_truncated=truncated,
        output_truncated_lines=dropped,
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
    """
    if max_lines is None or len(lines) <= max_lines:
        return lines, False, 0
    dropped = len(lines) - max_lines
    return lines[-max_lines:], True, dropped


def _filter_run_command_internal_lines(
    lines: list[str], channel: str, status_option: str
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
    internal_markers = (channel, status_option)
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
        :data:`CAPTURE_DEFAULT_MAX_LINES`. Pass ``None`` to return the
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
    """Clear the contents of a tmux pane.

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
    pane_id : str, optional
        Pane ID (e.g. '%1').
    bracket : bool
        Whether to use bracketed paste mode. Default True.
        Bracketed paste wraps the text in escape sequences that tell
        the terminal "this is pasted text, not typed input".
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
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

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

    return f"Text pasted to pane {pane.pane_id}"
