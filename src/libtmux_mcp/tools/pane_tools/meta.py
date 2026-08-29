"""Display-message and snapshot tools for pane introspection."""

from __future__ import annotations

from libtmux_mcp._bounded_io import (
    CAPTURE_DEFAULT_MAX_LINES,
    _truncate_lines_tail,
)
from libtmux_mcp._utils import (
    ExpectedToolError,
    _coerce_bool,
    _coerce_int,
    _compute_is_caller,
    _get_server,
    _resolve_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneSnapshot,
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
    """Evaluate a tmux format string against a target and return the expanded value.

    Read-only introspection tool — expands any tmux format variable
    against a target pane and returns the substituted text. Use this
    when no dedicated tool covers the field you want, e.g.
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
    if "#(" in format_string:
        msg = "tmux format jobs (#(...)) are not allowed in display_message"
        raise ExpectedToolError(msg)

    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    # ``--`` terminates flag parsing so a format that begins with a
    # dash reaches tmux as a format. Without it, format_string="-p" was
    # eaten as tmux's own print flag and tmux answered with its DEFAULT
    # message -- a plausible string answering a question nobody asked.
    # libtmux's display_message() cannot pass the terminator, hence the
    # direct cmd().
    result = pane.cmd("display-message", "-p", "--", format_string)
    if result.stderr:
        detail = "; ".join(result.stderr)
        msg = f"display-message failed: {detail}"
        raise ExpectedToolError(msg)
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
    """Snapshot a tmux pane: visible terminal output, cursor, mode, scroll.

    Use for terminal-contents inspection — 'what's in my pane', 'the
    current shell output' — not editor panes or browser viewports.
    Returns everything :func:`~libtmux_mcp.tools.pane_tools.capture_pane`
    and :func:`~libtmux_mcp.tools.pane_tools.get_pane_info` return, plus
    cursor position, copy-mode state, and scroll position — in a single
    call. Prefer this over separate capture_pane + get_pane_info calls
    when you need to reason about cursor location or pane mode.

    The ``content`` field is tail-preserved: when the captured pane
    exceeds ``max_lines``, the oldest lines are dropped and the result
    is reported via ``content_truncated`` / ``content_truncated_lines``
    fields on the returned :class:`~libtmux_mcp.models.PaneSnapshot`.
    Pass ``max_lines=None`` to opt out of truncation entirely.

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
        ``CAPTURE_DEFAULT_MAX_LINES``.
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
    _FMT_VARS = [
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
        "#{pane_left}",
        "#{pane_top}",
        "#{pane_right}",
        "#{pane_bottom}",
        "#{pane_at_left}",
        "#{pane_at_right}",
        "#{pane_at_top}",
        "#{pane_at_bottom}",
        "#{pane_tty}",
        "#{pane_pid}",
        "#{pane_dead}",
        "#{alternate_on}",
        "#{session_id}",
        "#{window_id}",
        "#{pane_index}",
        "#{pane_active}",
    ]
    fmt = _SEP.join(_FMT_VARS)
    stdout = pane.display_message(fmt, get_text=True)
    raw = stdout[0] if stdout else ""
    # Pad defensively to guarantee one slot per format var even if tmux
    # drops an unknown variable on older versions.
    parts = (raw.split(_SEP) + [""] * len(_FMT_VARS))[: len(_FMT_VARS)]
    # Keyed by name, not position. Positional indexing here silently
    # shifted every field below an inserted format var -- measured, a
    # newly added #{session_id} read back the pane index.
    values = {name[2:-1]: part for name, part in zip(_FMT_VARS, parts, strict=False)}

    raw_lines = pane.capture_pane()
    kept_lines, truncated, dropped = _truncate_lines_tail(raw_lines, max_lines)
    content = "\n".join(kept_lines)

    pane_in_mode = values["pane_in_mode"] == "1"
    pane_mode_raw = values["pane_mode"]
    scroll_raw = values["scroll_position"]

    return PaneSnapshot(
        pane_id=pane.pane_id or "",
        session_id=values["session_id"] or None,
        window_id=values["window_id"] or None,
        pane_index=values["pane_index"] or None,
        pane_active=_coerce_bool(values["pane_active"]),
        content=content,
        cursor_x=int(values["cursor_x"]) if values["cursor_x"] else 0,
        cursor_y=int(values["cursor_y"]) if values["cursor_y"] else 0,
        pane_width=int(values["pane_width"]) if values["pane_width"] else 0,
        pane_height=int(values["pane_height"]) if values["pane_height"] else 0,
        pane_in_mode=pane_in_mode,
        pane_mode=pane_mode_raw if pane_mode_raw else None,
        scroll_position=int(scroll_raw) if scroll_raw else None,
        history_size=int(values["history_size"]) if values["history_size"] else 0,
        title=values["pane_title"] if values["pane_title"] else None,
        pane_current_command=values["pane_current_command"]
        if values["pane_current_command"]
        else None,
        pane_current_path=values["pane_current_path"]
        if values["pane_current_path"]
        else None,
        pane_left=_coerce_int(values["pane_left"]),
        pane_top=_coerce_int(values["pane_top"]),
        pane_right=_coerce_int(values["pane_right"]),
        pane_bottom=_coerce_int(values["pane_bottom"]),
        pane_at_left=_coerce_bool(values["pane_at_left"]),
        pane_at_right=_coerce_bool(values["pane_at_right"]),
        pane_at_top=_coerce_bool(values["pane_at_top"]),
        pane_at_bottom=_coerce_bool(values["pane_at_bottom"]),
        pane_tty=values["pane_tty"] if values["pane_tty"] else None,
        pane_pid=values["pane_pid"] if values["pane_pid"] else None,
        pane_dead=_coerce_bool(values["pane_dead"]),
        alternate_on=_coerce_bool(values["alternate_on"]),
        is_caller=_compute_is_caller(pane),
        content_truncated=truncated,
        content_truncated_lines=dropped,
    )
