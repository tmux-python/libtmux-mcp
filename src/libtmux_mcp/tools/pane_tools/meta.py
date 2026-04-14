"""Display-message and snapshot tools for pane introspection."""

from __future__ import annotations

from libtmux_mcp._utils import (
    _get_caller_pane_id,
    _get_server,
    _resolve_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneSnapshot,
)
from libtmux_mcp.tools.pane_tools.io import (
    CAPTURE_DEFAULT_MAX_LINES,
    _truncate_lines_tail,
)


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
def snapshot_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    max_lines: int | None = CAPTURE_DEFAULT_MAX_LINES,
    socket_name: str | None = None,
) -> PaneSnapshot:
    """Take a rich snapshot of a tmux pane: content + cursor + mode + scroll state.

    Returns everything capture_pane and get_pane_info return, plus cursor
    position, copy-mode state, and scroll position — in a single call.
    Use this instead of separate capture_pane + get_pane_info calls when
    you need to reason about cursor location or pane mode.

    The ``content`` field is tail-preserved: when the captured pane
    exceeds ``max_lines``, the oldest lines are dropped and the result
    is reported via ``content_truncated`` / ``content_truncated_lines``
    fields on the returned :class:`PaneSnapshot`. Pass ``max_lines=None``
    to opt out of truncation entirely.

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
    max_lines : int or None
        Maximum number of content lines to return. Defaults to
        :data:`libtmux_mcp.tools.pane_tools.io.CAPTURE_DEFAULT_MAX_LINES`.
        Pass ``None`` to return the full capture untrimmed.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneSnapshot
        Rich snapshot with content, cursor, mode, and scroll state.
        When the capture is trimmed, ``content_truncated`` is True and
        ``content_truncated_lines`` gives the number of dropped head
        lines; ``content`` itself carries no marker header.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    # Fetch all metadata in a single display-message call. Use the
    # printable Unicode glyph ␞ (U+241E, "SYMBOL FOR RECORD SEPARATOR")
    # as the delimiter — the same choice libtmux itself uses for
    # FORMAT_SEPARATOR. tmux's utf8_strvis (tmux/utf8.c) copies any
    # valid UTF-8 multi-byte sequence verbatim, bypassing the vis()
    # escape that turns ASCII control chars like 0x1f into literal
    # "\037" in display-message output on some tmux builds. And ␞ is
    # safe against the false-positive path that a tab delimiter has:
    # tabs are legal (if rare) in Linux paths and could realistically
    # appear in pane_current_path.
    _SEP = "␞"
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

    raw_lines = pane.capture_pane()
    kept_lines, truncated, dropped = _truncate_lines_tail(raw_lines, max_lines)
    content = "\n".join(kept_lines)

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
        content_truncated=truncated,
        content_truncated_lines=dropped,
    )
