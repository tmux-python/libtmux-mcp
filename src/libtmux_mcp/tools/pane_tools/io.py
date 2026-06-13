"""I/O tools for tmux panes: send keys, capture output, paste, and clear."""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
import subprocess
import tempfile
import time
import uuid

from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    _tmux_argv,
    handle_tool_errors,
    handle_tool_errors_async,
)
from libtmux_mcp.models import RunCommandResult


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

    After sending, choose your synchronization primitive based on what you
    control:

    - **Deterministic (preferred):** compose ``tmux wait-for -S <channel>``
      into the shell command and call ``wait_for_channel``. See the
      ``run_and_wait`` prompt for the canonical safe-completion pattern.
      Cheaper in agent turns and immune to baseline races.
    - **Pattern-match:** call ``wait_for_text`` when the output you await
      is yours to author and won't appear before the wait locks its
      baseline (e.g. a sentinel ``echo`` after a long command). Fast
      ``echo`` statements can race the baseline read; reserve this for
      output the agent does not control.
    - **Any change:** call ``wait_for_content_change`` when you don't know
      the output shape.

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
        Whether to suppress shell history by prepending a space.
        Only works in shells that support HISTCONTROL. Default False.
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


@handle_tool_errors_async
async def run_command(
    command: str,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    timeout: float = 30.0,
    max_lines: int | None = None,
    socket_name: str | None = None,
) -> RunCommandResult:
    """Run a shell command in a pane, wait for completion, and capture output.

    Use for the common terminal workflow: run this command, wait until it
    completes, then report whether it succeeded. The command is sent to
    the pane's interactive shell, followed by a private ``tmux wait-for``
    signal and a private pane option carrying the shell exit status.

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
    command_id = uuid.uuid4().hex
    channel = f"libtmux_mcp_run_{command_id}"
    status_option = f"@libtmux_mcp_status_{command_id}"
    payload = "\n".join(
        (
            "{",
            command.rstrip(),
            (
                f"}}; __libtmux_mcp_status=$?; "
                f'tmux set-option -p {status_option} "$__libtmux_mcp_status"; '
                f"tmux wait-for -S {channel}"
            ),
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

    # join_wrapped keeps the private sync line a single logical row, so
    # _filter_run_command_internal_lines can match it by the per-call
    # channel/status option even when a wide prompt wraps it.
    raw_lines = await asyncio.to_thread(pane.capture_pane, join_wrapped=True)
    visible_lines = _filter_run_command_internal_lines(
        raw_lines,
        channel=channel,
        status_option=status_option,
    )
    kept_lines, truncated, dropped = _truncate_lines_tail(visible_lines, max_lines)
    return RunCommandResult(
        pane_id=pane.pane_id or "",
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
    """Drop the private synchronisation line from captured output.

    Matches only the per-call ``channel`` and ``status_option`` (random
    hex that never collides with real output). ``run_command`` captures
    with ``join_wrapped`` so the line stays one logical row even under a
    wide prompt, keeping both markers intact.
    """
    internal_markers = (channel, status_option)
    return [
        line for line in lines if not any(marker in line for marker in internal_markers)
    ]


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

    Use before send_keys + capture_pane to get a clean capture without prior output.

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
