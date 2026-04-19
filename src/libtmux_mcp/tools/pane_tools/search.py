"""Content-search across tmux panes."""

from __future__ import annotations

import re

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _compute_is_caller,
    _get_server,
    _resolve_session,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneContentMatch,
    SearchPanesResult,
)

#: Default per-pane cap on returned ``matched_lines``. Keep the tail
#: (most-recent) matches so the agent sees what's currently on screen.
SEARCH_DEFAULT_MAX_LINES_PER_PANE = 50

#: Default maximum number of matching panes returned in one call.
#: Pagination via ``offset``/``limit`` lets the caller page forward.
SEARCH_DEFAULT_LIMIT = 500


def _pane_id_sort_key(m: PaneContentMatch) -> tuple[int, str]:
    """Sort panes numerically by their tmux id.

    tmux pane ids are strings like ``"%7"`` — a plain lex sort produces
    ``["%0", "%1", "%10", "%2", ...]``, which is surprising to callers
    paginating with ``offset``/``limit``. Strip the leading ``%`` and
    cast to int so ``"%2"`` sorts before ``"%10"``; fall back to lex
    order for any non-standard id (the tuple's first element ensures
    numeric ids always precede weird ids).

    Examples
    --------
    >>> from libtmux_mcp.models import PaneContentMatch
    >>> ids = ["%0", "%10", "%2", "%20"]
    >>> [
    ...     m.pane_id
    ...     for m in sorted(
    ...         [PaneContentMatch(pane_id=i, matched_lines=[]) for i in ids],
    ...         key=_pane_id_sort_key,
    ...     )
    ... ]
    ['%0', '%2', '%10', '%20']

    Non-standard ids fall to the tail in lex order:

    >>> [
    ...     m.pane_id
    ...     for m in sorted(
    ...         [
    ...             PaneContentMatch(pane_id=i, matched_lines=[])
    ...             for i in ["zzz", "%0", "weird"]
    ...         ],
    ...         key=_pane_id_sort_key,
    ...     )
    ... ]
    ['%0', 'weird', 'zzz']
    """
    pid = m.pane_id.lstrip("%")
    try:
        return (0, f"{int(pid):09d}")
    except ValueError:
        return (1, m.pane_id)


@handle_tool_errors
def search_panes(
    pattern: str,
    regex: bool = False,
    session_name: str | None = None,
    session_id: str | None = None,
    match_case: bool = False,
    content_start: int | None = None,
    content_end: int | None = None,
    max_matched_lines_per_pane: int = SEARCH_DEFAULT_MAX_LINES_PER_PANE,
    limit: int | None = SEARCH_DEFAULT_LIMIT,
    offset: int = 0,
    socket_name: str | None = None,
) -> SearchPanesResult:
    """Search for text across all pane contents.

    Use this when users ask what panes 'contain', 'mention', or 'show'.
    Searches each pane's visible content and returns panes where the
    pattern is found, with matching lines.

    Bounded output contract
    -----------------------
    The result is paginated at the **pane** level. The matching panes
    are sorted by ``pane_id`` and then sliced with ``offset`` /
    ``limit``. Each matching pane's ``matched_lines`` is further
    tail-truncated to at most ``max_matched_lines_per_pane`` entries
    (most-recent lines preserved). Caps apply only to the slow path
    (``pane.capture_pane()`` + Python regex); the tmux fast path at
    ``#{C:pattern}`` returns pane IDs only and is already bounded by
    tmux.

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
    max_matched_lines_per_pane : int
        Per-pane cap on ``matched_lines``. Defaults to
        :data:`SEARCH_DEFAULT_MAX_LINES_PER_PANE`.
    limit : int or None
        Maximum matching panes returned on this call. Defaults to
        :data:`SEARCH_DEFAULT_LIMIT`. Pass ``None`` to disable the cap.
    offset : int
        Skip this many matching panes from the start. Use with
        ``limit`` for pagination.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    SearchPanesResult
        Paginated match list with ``truncated`` / ``truncated_panes``
        / ``total_panes_matched`` / ``offset`` / ``limit`` fields.
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

    # Decide whether the tmux-side ``#{C:...}`` fast path can safely
    # serve the query. Two distinct hazards gate the decision:
    #
    # 1. Regex metacharacters in a ``regex=True`` pattern — tmux's glob
    #    matcher cannot interpret them, so they must take the slow
    #    Python-regex path. Checked against the raw ``pattern``, NOT
    #    the escaped ``search_pattern``; the previous form incorrectly
    #    tested ``re.escape(pattern)``, so any literal input that
    #    happened to contain a metacharacter (e.g. "192.168.1.1" →
    #    "192\\.168\\.1\\.1" — now matches because of ``\\``) was
    #    pushed onto the slow path.
    #
    # 2. tmux format-string injection — ``#{C:pattern}`` is a tmux
    #    format block. ``}`` in the pattern closes the block early
    #    (evaluated as truthy, matching every pane as a false
    #    positive); ``#{`` inside the pattern starts a nested format
    #    variable. tmux provides no escape mechanism for these bytes
    #    inside the format block, so the only safe option is to route
    #    around: when the raw pattern contains either sequence, fall
    #    through to the slow Python-regex path. This applies whether
    #    or not ``regex`` is True — the injection risk is tmux-side,
    #    not regex-side.
    _REGEX_META = re.compile(r"[\\.*+?{}()\[\]|^$]")
    _TMUX_FORMAT_INJECTION = re.compile(r"\}|#\{")
    if _TMUX_FORMAT_INJECTION.search(pattern):
        is_plain_text = False
    elif regex:
        is_plain_text = not _REGEX_META.search(pattern)
    else:
        is_plain_text = True

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

    # Phase 2: Capture matching panes, extract matched lines, and
    # apply bounded-output caps. Pagination is at the pane level:
    # sort the matching panes by pane_id for deterministic ordering,
    # then slice by offset / limit. Per-pane matched_lines is
    # tail-truncated to keep the most recent matches.
    all_matches: list[PaneContentMatch] = []
    per_pane_truncated = False
    for pane_id_str in matching_pane_ids:
        pane = server.panes.get(pane_id=pane_id_str, default=None)
        if pane is None:
            continue

        lines = pane.capture_pane(start=content_start, end=content_end)
        matched_lines = [line for line in lines if compiled.search(line)]

        if not matched_lines:
            continue

        if len(matched_lines) > max_matched_lines_per_pane:
            matched_lines = matched_lines[-max_matched_lines_per_pane:]
            per_pane_truncated = True

        window = pane.window
        session_obj = pane.session
        all_matches.append(
            PaneContentMatch(
                pane_id=pane_id_str,
                pane_current_command=getattr(pane, "pane_current_command", None),
                pane_current_path=getattr(pane, "pane_current_path", None),
                window_id=pane.window_id,
                window_name=getattr(window, "window_name", None),
                session_id=pane.session_id,
                session_name=getattr(session_obj, "session_name", None),
                matched_lines=matched_lines,
                is_caller=_compute_is_caller(pane),
            )
        )

    all_matches.sort(key=_pane_id_sort_key)
    total_panes_matched = len(all_matches)

    page_start = max(0, offset)
    page_end: int | None = None if limit is None else page_start + max(0, limit)
    page_matches = all_matches[page_start:page_end]

    skipped_panes = [m.pane_id for m in all_matches[page_start:][len(page_matches) :]]
    global_truncated = bool(skipped_panes)

    return SearchPanesResult(
        matches=page_matches,
        truncated=per_pane_truncated or global_truncated,
        truncated_panes=skipped_panes,
        total_panes_matched=total_panes_matched,
        offset=offset,
        limit=limit,
    )
