"""Content-search across tmux panes."""

from __future__ import annotations

import re

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _get_caller_pane_id,
    _get_server,
    _resolve_session,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneContentMatch,
)


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
