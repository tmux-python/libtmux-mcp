"""MCP tools for tmux pane operations."""

from __future__ import annotations

import contextlib
import pathlib
import re
import shlex
import typing as t
import uuid

from libtmux_mcp._utils import (
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    _get_caller_pane_id,
    _get_server,
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    ContentChangeResult,
    PaneContentMatch,
    PaneInfo,
    PaneSnapshot,
    WaitForTextResult,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


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
def resize_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    height: int | None = None,
    width: int | None = None,
    zoom: bool | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Resize a tmux pane.

    Use when adjusting layout for better readability or to fit content.

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
    height : int, optional
        New height in lines.
    width : int, optional
        New width in columns.
    zoom : bool, optional
        Toggle pane zoom. If True, zoom the pane. If False, unzoom.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane object.
    """
    from fastmcp.exceptions import ToolError

    if zoom is not None and (height is not None or width is not None):
        msg = "Cannot combine zoom with height/width"
        raise ToolError(msg)

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    if zoom is not None:
        window = pane.window
        result = window.cmd("display-message", "-p", "#{window_zoomed_flag}")
        is_zoomed = bool(result.stdout) and result.stdout[0] == "1"
        if zoom and not is_zoomed:
            pane.resize(zoom=True)
        elif not zoom and is_zoomed:
            pane.resize(zoom=True)  # toggle off
    else:
        pane.resize(height=height, width=width)
    return _serialize_pane(pane)


@handle_tool_errors
def kill_pane(
    pane_id: str,
    socket_name: str | None = None,
) -> str:
    """Kill (close) a tmux pane. Requires exact pane_id (e.g. '%5').

    Use to clean up panes no longer needed. To remove an entire window
    and all its panes, use kill_window instead.

    Parameters
    ----------
    pane_id : str
        Pane ID (e.g. '%1'). Required — no fallback resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    from fastmcp.exceptions import ToolError

    caller = _get_caller_pane_id()
    if caller is not None and pane_id == caller:
        msg = (
            "Refusing to kill the pane running this MCP server. "
            "Use a manual tmux command if intended."
        )
        raise ToolError(msg)

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(server, pane_id=pane_id)
    pid = pane.pane_id
    pane.kill()
    return f"Pane killed: {pid}"


@handle_tool_errors
def set_pane_title(
    title: str,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Set the title of a tmux pane.

    Use titles to label panes for later identification via list_panes or get_pane_info.

    Parameters
    ----------
    title : str
        The new pane title.
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
    PaneInfo
        Serialized pane object.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.set_title(title)
    return _serialize_pane(pane)


@handle_tool_errors
def get_pane_info(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Get detailed information about a tmux pane.

    Use this for metadata (PID, path, dimensions) without reading terminal content.
    To read what is displayed in the pane, use capture_pane instead.

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
    PaneInfo
        Serialized pane details.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    return _serialize_pane(pane)


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
def search_panes(
    pattern: str,
    regex: bool = False,
    session_name: str | None = None,
    session_id: str | None = None,
    match_case: bool = False,
    content_start: int | None = None,
    content_end: int | None = None,
    socket_name: str | None = None,
) -> list[PaneContentMatch]:
    """Search for text across all pane contents.

    Use this when users ask what panes 'contain', 'mention', or 'show'.
    Searches each pane's visible content and returns panes where the
    pattern is found, with matching lines.

    Parameters
    ----------
    pattern : str
        Text to search for in pane contents. Treated as literal text by
        default. Set ``regex=True`` to interpret as a regular expression.
    regex : bool
        Whether to interpret pattern as a regular expression. Default False
        (literal text matching).
    session_name : str, optional
        Limit search to panes in this session.
    session_id : str, optional
        Limit search to panes in this session (by ID).
    match_case : bool
        Whether to match case. Default False (case-insensitive).
    content_start : int, optional
        Start line for capture. Negative values reach into scrollback.
    content_end : int, optional
        End line for capture.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    list[PaneContentMatch]
        Panes with matching content, including matched lines.
    """
    from fastmcp.exceptions import ToolError

    search_pattern = pattern if regex else re.escape(pattern)
    flags = 0 if match_case else re.IGNORECASE
    try:
        compiled = re.compile(search_pattern, flags)
    except re.error as e:
        msg = f"Invalid regex pattern: {e}"
        raise ToolError(msg) from e

    server = _get_server(socket_name=socket_name)

    uses_scrollback = content_start is not None or content_end is not None

    # Detect if the effective pattern contains regex metacharacters that
    # would break tmux's glob-based #{C:} filter. When regex is needed,
    # skip the tmux fast path and capture all panes for Python-side matching.
    _REGEX_META = re.compile(r"[\\.*+?{}()\[\]|^$]")
    is_plain_text = not _REGEX_META.search(search_pattern)

    if not uses_scrollback and is_plain_text:
        # Phase 1: Fast filter via tmux's C-level window_pane_search().
        # #{C/i:pattern} searches visible pane content in C, returning only
        # matching pane IDs without capturing full content.
        case_flag = "" if match_case else "i"
        tmux_filter = (
            f"#{{C/{case_flag}:{pattern}}}" if case_flag else f"#{{C:{pattern}}}"
        )

        cmd_args: list[str] = ["list-panes"]
        if session_name is not None or session_id is not None:
            session = _resolve_session(
                server, session_name=session_name, session_id=session_id
            )
            cmd_args.extend(["-t", session.session_id or ""])
            cmd_args.append("-s")
        else:
            cmd_args.append("-a")
        cmd_args.extend(["-f", tmux_filter, "-F", "#{pane_id}"])

        result = server.cmd(*cmd_args)
        matching_pane_ids = list(dict.fromkeys(result.stdout)) if result.stdout else []
    else:
        # Regex pattern or scrollback requested — fall back to capturing
        # all panes and matching in Python.
        if session_name is not None or session_id is not None:
            session = _resolve_session(
                server, session_name=session_name, session_id=session_id
            )
            all_panes = session.panes
        else:
            all_panes = server.panes
        matching_pane_ids = list(
            dict.fromkeys(p.pane_id for p in all_panes if p.pane_id is not None)
        )

    # Phase 2: Capture matching panes and extract matched lines.
    caller_pane_id = _get_caller_pane_id()
    matches: list[PaneContentMatch] = []
    for pane_id_str in matching_pane_ids:
        pane = server.panes.get(pane_id=pane_id_str, default=None)
        if pane is None:
            continue

        lines = pane.capture_pane(start=content_start, end=content_end)
        matched_lines = [line for line in lines if compiled.search(line)]

        if not matched_lines:
            continue

        window = pane.window
        session_obj = pane.session
        matches.append(
            PaneContentMatch(
                pane_id=pane_id_str,
                pane_current_command=getattr(pane, "pane_current_command", None),
                pane_current_path=getattr(pane, "pane_current_path", None),
                window_id=pane.window_id,
                window_name=getattr(window, "window_name", None),
                session_id=pane.session_id,
                session_name=getattr(session_obj, "session_name", None),
                matched_lines=matched_lines,
                is_caller=(pane_id_str == caller_pane_id if caller_pane_id else None),
            )
        )

    matches.sort(key=lambda m: m.pane_id)
    return matches


@handle_tool_errors
def wait_for_text(
    pattern: str,
    regex: bool = False,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    timeout: float = 8.0,
    interval: float = 0.05,
    match_case: bool = False,
    content_start: int | None = None,
    content_end: int | None = None,
    socket_name: str | None = None,
) -> WaitForTextResult:
    """Wait for text to appear in a tmux pane.

    Polls the pane content at regular intervals until the pattern is found
    or the timeout is reached. Use this instead of polling capture_pane
    manually — it saves agent tokens and turns.

    Parameters
    ----------
    pattern : str
        Text to wait for. Treated as literal text by default. Set
        ``regex=True`` to interpret as a regular expression.
    regex : bool
        Whether to interpret pattern as a regular expression. Default False
        (literal text matching).
    pane_id : str, optional
        Pane ID (e.g. '%1').
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID (e.g. '$1') for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    timeout : float
        Maximum seconds to wait. Default 8.0.
    interval : float
        Seconds between polls. Default 0.05 (50ms).
    match_case : bool
        Whether to match case. Default False (case-insensitive).
    content_start : int, optional
        Start line for capture. Negative values reach into scrollback.
    content_end : int, optional
        End line for capture.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WaitForTextResult
        Result with found status, matched lines, and timing info.
    """
    import time

    from fastmcp.exceptions import ToolError
    from libtmux.test.retry import retry_until

    search_pattern = pattern if regex else re.escape(pattern)
    flags = 0 if match_case else re.IGNORECASE
    try:
        compiled = re.compile(search_pattern, flags)
    except re.error as e:
        msg = f"Invalid regex pattern: {e}"
        raise ToolError(msg) from e

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    assert pane.pane_id is not None
    matched_lines: list[str] = []
    start_time = time.monotonic()

    def _check() -> bool:
        lines = pane.capture_pane(start=content_start, end=content_end)
        hits = [line for line in lines if compiled.search(line)]
        if hits:
            matched_lines.extend(hits)
            return True
        return False

    found = retry_until(
        _check,
        seconds=timeout,
        interval=interval,
        raises=False,
    )

    elapsed = time.monotonic() - start_time
    return WaitForTextResult(
        found=found,
        matched_lines=matched_lines,
        pane_id=pane.pane_id,
        elapsed_seconds=round(elapsed, 3),
        timed_out=not found,
    )


@handle_tool_errors
def snapshot_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneSnapshot:
    """Take a rich snapshot of a tmux pane: content + cursor + mode + scroll state.

    Returns everything capture_pane and get_pane_info return, plus cursor
    position, copy-mode state, and scroll position — in a single call.
    Use this instead of separate capture_pane + get_pane_info calls when
    you need to reason about cursor location or pane mode.

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
    PaneSnapshot
        Rich snapshot with content, cursor, mode, and scroll state.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    # Fetch all metadata in a single display-message call. Use a tab as
    # the delimiter: tmux passes tabs through verbatim in
    # display-message output, whereas other ASCII control characters
    # (e.g. 0x1f / Unit Separator) get C-escaped to literal "\037"
    # strings on tmux >=3.2 / <3.6-rc, which corrupts parsing. Tabs in
    # pane_title are silently rejected by tmux's `select-pane -T`
    # input sanitizer, so the `\t` delimiter is safe against that
    # vector. Tabs in pane_current_path are legal on Linux but
    # vanishingly rare; the defensive padding below limits the blast
    # radius to a single truncated field rather than an IndexError.
    _SEP = "\t"
    fmt = _SEP.join(
        [
            "#{cursor_x}",
            "#{cursor_y}",
            "#{pane_width}",
            "#{pane_height}",
            "#{pane_in_mode}",
            "#{pane_mode}",
            "#{scroll_position}",
            "#{history_size}",
            "#{pane_title}",
            "#{pane_current_command}",
            "#{pane_current_path}",
        ]
    )
    result = pane.cmd("display-message", "-p", "-t", pane.pane_id, fmt)
    raw = result.stdout[0] if result.stdout else ""
    # Pad defensively to guarantee 11 fields even if tmux drops an
    # unknown format variable on older versions.
    parts = (raw.split(_SEP) + [""] * 11)[:11]

    content = "\n".join(pane.capture_pane())

    pane_in_mode = parts[4] == "1"
    pane_mode_raw = parts[5]
    scroll_raw = parts[6]

    caller_pane_id = _get_caller_pane_id()
    return PaneSnapshot(
        pane_id=pane.pane_id or "",
        content=content,
        cursor_x=int(parts[0]) if parts[0] else 0,
        cursor_y=int(parts[1]) if parts[1] else 0,
        pane_width=int(parts[2]) if parts[2] else 0,
        pane_height=int(parts[3]) if parts[3] else 0,
        pane_in_mode=pane_in_mode,
        pane_mode=pane_mode_raw if pane_mode_raw else None,
        scroll_position=int(scroll_raw) if scroll_raw else None,
        history_size=int(parts[7]) if parts[7] else 0,
        title=parts[8] if parts[8] else None,
        pane_current_command=parts[9] if parts[9] else None,
        pane_current_path=parts[10] if parts[10] else None,
        is_caller=(pane.pane_id == caller_pane_id if caller_pane_id else None),
    )


@handle_tool_errors
def wait_for_content_change(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    timeout: float = 8.0,
    interval: float = 0.05,
    socket_name: str | None = None,
) -> ContentChangeResult:
    """Wait for any content change in a tmux pane.

    Captures the current pane content, then polls until the content differs
    or the timeout is reached. Use this after send_keys when you don't know
    what the output will be — it waits for "something happened" rather than
    a specific pattern.

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
    timeout : float
        Maximum seconds to wait. Default 8.0.
    interval : float
        Seconds between polls. Default 0.05 (50ms).
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    ContentChangeResult
        Result with changed status and timing info.
    """
    import time

    from libtmux.test.retry import retry_until

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    assert pane.pane_id is not None
    initial_content = pane.capture_pane()
    start_time = time.monotonic()

    def _check() -> bool:
        current = pane.capture_pane()
        return current != initial_content

    changed = retry_until(
        _check,
        seconds=timeout,
        interval=interval,
        raises=False,
    )

    elapsed = time.monotonic() - start_time
    return ContentChangeResult(
        changed=changed,
        pane_id=pane.pane_id,
        elapsed_seconds=round(elapsed, 3),
        timed_out=not changed,
    )


@handle_tool_errors
def select_pane(
    pane_id: str | None = None,
    direction: t.Literal["up", "down", "left", "right", "last", "next", "previous"]
    | None = None,
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Select (focus) a tmux pane by ID or direction.

    Use this to navigate between panes. Provide either pane_id for direct
    selection, or direction for relative navigation within a window.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1') for direct selection.
    direction : str, optional
        Relative direction: 'up', 'down', 'left', 'right', 'last'
        (previously active), 'next', or 'previous'.
    window_id : str, optional
        Window ID for directional navigation scope.
    window_index : str, optional
        Window index for directional navigation scope.
    session_name : str, optional
        Session name for resolution.
    session_id : str, optional
        Session ID for resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        The now-active pane.
    """
    from fastmcp.exceptions import ToolError

    if pane_id is None and direction is None:
        msg = "Provide either pane_id or direction."
        raise ToolError(msg)

    server = _get_server(socket_name=socket_name)

    if pane_id is not None:
        pane = _resolve_pane(server, pane_id=pane_id)
        pane.select()
        return _serialize_pane(pane)

    # Directional navigation
    _DIRECTION_FLAGS: dict[str, str] = {
        "up": "-U",
        "down": "-D",
        "left": "-L",
        "right": "-R",
        "last": "-l",
    }

    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )

    assert direction is not None
    if direction in _DIRECTION_FLAGS:
        window.select_pane(_DIRECTION_FLAGS[direction])
    elif direction == "next":
        # Anchor the relative target to the requested window. A bare
        # `-t +` resolves against the attached client's current window
        # (tmux cmd-find.c), NOT the window we're targeting.
        # `@window_id.+` forces tmux to resolve the `+` offset against
        # the explicit window's active pane.
        server.cmd("select-pane", target=f"{window.window_id}.+")
    elif direction == "previous":
        server.cmd("select-pane", target=f"{window.window_id}.-")

    # Query the active pane ID directly from tmux to avoid stale cache
    target = window.window_id or ""
    result = window.cmd("display-message", "-p", "-t", target, "#{pane_id}")
    active_pane_id = result.stdout[0] if result.stdout else None
    if active_pane_id:
        active_pane = server.panes.get(pane_id=active_pane_id, default=None)
        if active_pane is not None:
            return _serialize_pane(active_pane)

    # Fallback
    active_pane = window.active_pane
    assert active_pane is not None
    return _serialize_pane(active_pane)


@handle_tool_errors
def swap_pane(
    source_pane_id: str,
    target_pane_id: str,
    socket_name: str | None = None,
) -> PaneInfo:
    """Swap the positions of two panes.

    Exchanges the visual positions of two panes. Both panes must exist.
    Use this to rearrange pane layout without changing content.

    Parameters
    ----------
    source_pane_id : str
        Pane ID of the first pane (e.g. '%1').
    target_pane_id : str
        Pane ID of the second pane (e.g. '%2').
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        The source pane after swap (now in target's position).
    """
    server = _get_server(socket_name=socket_name)
    # Validate both panes exist
    source = _resolve_pane(server, pane_id=source_pane_id)
    _resolve_pane(server, pane_id=target_pane_id)

    server.cmd("swap-pane", "-s", source_pane_id, "-t", target_pane_id)
    source.refresh()
    return _serialize_pane(source)


@handle_tool_errors
def pipe_pane(
    pane_id: str | None = None,
    output_path: str | None = None,
    append: bool = True,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> str:
    """Start or stop piping pane output to a file.

    When output_path is given, starts logging all pane output to the file.
    When output_path is None, stops any active pipe for the pane.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    output_path : str, optional
        File path to write output to. None stops piping.
    append : bool
        Whether to append to the file. Default True. If False, overwrites.
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

    if output_path is None:
        pane.cmd("pipe-pane")
        return f"Piping stopped for pane {pane.pane_id}"

    if not output_path.strip():
        from fastmcp.exceptions import ToolError

        msg = "output_path must be a non-empty path, or None to stop piping."
        raise ToolError(msg)

    redirect = ">>" if append else ">"
    pane.cmd("pipe-pane", f"cat {redirect} {shlex.quote(output_path)}")
    return f"Piping pane {pane.pane_id} to {output_path}"


@handle_tool_errors
def display_message(
    format_string: str,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> str:
    """Query tmux using a format string.

    Expands tmux format variables against a target pane. Use this as a
    generic introspection tool to query any tmux variable, e.g.
    '#{window_zoomed_flag}', '#{pane_dead}', '#{client_activity}'.

    Parameters
    ----------
    format_string : str
        tmux format string (e.g. '#{cursor_x} #{cursor_y}').
    pane_id : str, optional
        Pane ID (e.g. '%1').
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
        Expanded format string result.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    result = pane.cmd("display-message", "-p", "-t", pane.pane_id, format_string)
    return "\n".join(result.stdout) if result.stdout else ""


@handle_tool_errors
def enter_copy_mode(
    pane_id: str | None = None,
    scroll_up: int | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Enter copy mode in a tmux pane, optionally scrolling up.

    Use to navigate scrollback history. After entering copy mode, use
    snapshot_pane to read the scroll_position and content.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    scroll_up : int, optional
        Number of lines to scroll up immediately after entering copy mode.
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
    PaneInfo
        Serialized pane info.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.cmd("copy-mode", "-t", pane.pane_id)
    if scroll_up is not None and scroll_up > 0:
        pane.cmd(
            "send-keys",
            "-X",
            "-N",
            str(scroll_up),
            "scroll-up",
        )
    pane.refresh()
    return _serialize_pane(pane)


@handle_tool_errors
def exit_copy_mode(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Exit copy mode in a tmux pane.

    Returns the pane to normal mode. Use after scrolling through
    scrollback history.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
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
    PaneInfo
        Serialized pane info.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    pane.cmd("send-keys", "-t", pane.pane_id, "-X", "cancel")
    pane.refresh()
    return _serialize_pane(pane)


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
    import subprocess
    import tempfile

    from fastmcp.exceptions import ToolError

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


def register(mcp: FastMCP) -> None:
    """Register pane-level tools with the MCP instance."""
    mcp.tool(title="Send Keys", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING})(
        send_keys
    )
    mcp.tool(title="Capture Pane", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        capture_pane
    )
    mcp.tool(
        title="Resize Pane", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(resize_pane)
    mcp.tool(
        title="Kill Pane",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(kill_pane)
    mcp.tool(
        title="Set Pane Title", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(set_pane_title)
    mcp.tool(title="Get Pane Info", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        get_pane_info
    )
    mcp.tool(title="Clear Pane", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING})(
        clear_pane
    )
    mcp.tool(title="Search Panes", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        search_panes
    )
    mcp.tool(title="Wait For Text", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        wait_for_text
    )
    mcp.tool(title="Snapshot Pane", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        snapshot_pane
    )
    mcp.tool(
        title="Wait For Content Change",
        annotations=ANNOTATIONS_RO,
        tags={TAG_READONLY},
    )(wait_for_content_change)
    mcp.tool(
        title="Select Pane", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(select_pane)
    mcp.tool(title="Swap Pane", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING})(
        swap_pane
    )
    mcp.tool(title="Pipe Pane", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING})(
        pipe_pane
    )
    mcp.tool(title="Display Message", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        display_message
    )
    mcp.tool(
        title="Enter Copy Mode",
        annotations=ANNOTATIONS_CREATE,
        tags={TAG_MUTATING},
    )(enter_copy_mode)
    mcp.tool(
        title="Exit Copy Mode",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(exit_copy_mode)
    mcp.tool(title="Paste Text", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING})(
        paste_text
    )
