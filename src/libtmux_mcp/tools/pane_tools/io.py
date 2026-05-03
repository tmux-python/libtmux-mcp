"""I/O tools for tmux panes: send keys, capture output, paste, and clear."""

from __future__ import annotations

import contextlib
import pathlib
import subprocess
import tempfile
import uuid

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _get_server,
    _resolve_pane,
    _tmux_argv,
    handle_tool_errors,
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

    After sending, use wait_for_text to block until the command completes
    (server-side, turn-cheap) or capture_pane once you know it has
    finished. Do not capture_pane in a tight loop — that races with
    command execution and burns agent turns; wait_for_text is the
    server-side blocking primitive built for this flow.

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
    """Capture the visible contents of a tmux pane.

    This is the tool for reading what is displayed in a terminal. Use
    search_panes to search for text across multiple panes at once.

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
    Note: this is two tmux commands with a brief gap — not fully atomic.

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
    # Split into two cmd() calls — pane.reset() in libtmux <= 0.55.0 sends
    # `send-keys -R \; clear-history` as one call, but subprocess doesn't
    # interpret \; as a tmux command separator so clear-history never runs.
    # See: https://github.com/tmux-python/libtmux/issues/650
    pane.cmd("send-keys", "-R")
    pane.cmd("clear-history")
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
            raise ToolError(msg) from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
            msg = f"load-buffer failed: {stderr or e}"
            raise ToolError(msg) from e

        # Paste from the named buffer. -d deletes only that named buffer,
        # leaving any unnamed user buffer intact.
        paste_args = ["-b", buffer_name, "-d"]
        if bracket:
            paste_args.append("-p")  # bracketed paste mode
        paste_args.extend(["-t", pane.pane_id or ""])
        pane.cmd("paste-buffer", *paste_args)
    finally:
        if tmppath is not None:
            pathlib.Path(tmppath).unlink(missing_ok=True)
        # Defensive: the buffer should already be gone (paste-buffer -d
        # deletes it), but if paste-buffer failed before -d took effect
        # we leak an entry in the tmux server. Best-effort delete.
        with contextlib.suppress(Exception):
            server.cmd("delete-buffer", "-b", buffer_name)

    return f"Text pasted to pane {pane.pane_id}"
