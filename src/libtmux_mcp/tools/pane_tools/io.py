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

    After sending, use wait_for_text to block until the command completes,
    or capture_pane to read the result. Do not capture_pane immediately —
    there is a race condition.

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


@handle_tool_errors
def capture_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    start: int | None = None,
    end: int | None = None,
    socket_name: str | None = None,
) -> str:
    """Capture the visible contents of a tmux pane.

    This is the tool for reading what is displayed in a terminal. Use
    search_panes to search for text across multiple panes at once.

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
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Captured pane content as text.
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
    return "\n".join(lines)


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
    # paths (paste-buffer -b NAME -d deletes the named buffer).
    buffer_name = f"mcp_paste_{uuid.uuid4().hex}"
    tmppath: str | None = None
    try:
        # Write text to a temp file and load into tmux buffer
        # (libtmux's cmd() doesn't support stdin).
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmppath = f.name  # bind first so cleanup works even if write fails
            f.write(text)

        # Build tmux command args for loading the named buffer
        tmux_bin: str = getattr(server, "tmux_bin", None) or "tmux"
        load_args: list[str] = [tmux_bin]
        if server.socket_name:
            load_args.extend(["-L", server.socket_name])
        if server.socket_path:
            load_args.extend(["-S", str(server.socket_path)])
        load_args.extend(["load-buffer", "-b", buffer_name, tmppath])

        try:
            subprocess.run(load_args, check=True, capture_output=True)
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
