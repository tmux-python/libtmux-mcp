"""Content-search across tmux panes."""

from __future__ import annotations

import re

from libtmux_mcp._patterns import compile_pattern
from libtmux_mcp._utils import (
    ExpectedToolError,
    _coerce_bool,
    _coerce_int,
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
    """Search visible terminal text across all tmux panes.

    Use when the user asks what panes 'contain', 'mention', or 'show' —
    e.g. 'find the pane with the pytest failure'. Returns panes where
    the pattern is found, with matching lines (tmux panes only, not
    editor or browser text).

    **Scope: the visible screen only, by default.** Scrollback is NOT
    searched unless ``content_start`` is given, so a match that has
    already scrolled off returns ``matches: []`` — which does not mean
    the text is absent. The result reports ``searched_scope`` so this is
    visible at the call site; pass ``content_start=-500`` (or further)
    to include scrollback. It stays opt-in because this tool fans out
    across every pane on the server, and defaulting to scrollback would
    multiply cost by history depth times pane count.

    Bounded output contract
    -----------------------
    The result is paginated at the **pane** level. The matching panes
    are sorted by ``pane_id`` and then sliced with ``offset`` /
    ``limit``. Each matching pane's ``matched_lines`` is further
    tail-truncated to at most ``max_matched_lines_per_pane`` entries
    (most-recent lines preserved). Caps apply only to the slow path
    (``pane.capture_pane(join_wrapped=True)`` + Python regex); the tmux
    fast path at ``#{C:pattern}`` returns pane IDs only and is already
    bounded by tmux.
    The slow path joins wrapped visual rows so long lines can match
    across the pane's wrap column. The fast path remains tmux's native
    visual-row search, so use ``regex=True`` or an explicit content
    range to force the slow path when wrap-spanning text matters.

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
        ``SEARCH_DEFAULT_MAX_LINES_PER_PANE``.
    limit : int or None
        Maximum matching panes returned on this call. Defaults to
        ``SEARCH_DEFAULT_LIMIT``. Pass ``None`` to disable the cap.
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
    flags = 0 if match_case else re.IGNORECASE
    compiled = compile_pattern(pattern, regex=regex, flags=flags, label="search")

    # Reject nonsense pagination rather than answer with an empty page:
    # ``limit=0`` is indistinguishable from a genuine miss, and a negative
    # ``offset`` clamped to 0 answers a different request than it echoes.
    if offset < 0:
        msg = f"offset must be zero or greater (received {offset})"
        raise ExpectedToolError(msg)
    if limit is not None and limit < 1:
        msg = f"limit must be at least 1, or null for no limit (received {limit})"
        raise ExpectedToolError(msg)

    server = _get_server(socket_name=socket_name)

    uses_scrollback = content_start is not None or content_end is not None

    # Two hazards gate the tmux-side ``#{C:...}`` fast path:
    #
    # 1. Regex metacharacters under ``regex=True`` -- tmux's glob matcher
    #    cannot interpret them. Tested against the raw ``pattern``, never
    #    the escaped ``search_pattern``, whose own backslashes would push
    #    every literal containing a dot onto the slow path.
    #
    # 2. tmux format-string injection -- ``}`` closes the format block
    #    early and matches every pane, ``#{`` nests a format variable,
    #    ``#(`` runs a shell job. tmux offers no escape for these inside
    #    a format block, so the only safe move is the slow path. Applies
    #    regardless of ``regex``: the risk is tmux-side.
    _REGEX_META = re.compile(r"[\\.*+?{}()\[\]|^$]")
    _TMUX_FORMAT_INJECTION = re.compile(r"\}|#\{|#\(")
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

    # Pagination is at the PANE level: sorted by pane_id for determinism,
    # then sliced by offset/limit. Per-pane matched_lines is tail-
    # truncated, keeping the most recent matches.
    all_matches: list[PaneContentMatch] = []
    per_pane_truncated = False
    for pane_id_str in matching_pane_ids:
        pane = server.panes.get(pane_id=pane_id_str, default=None)
        if pane is None:
            continue

        lines = pane.capture_pane(
            start=content_start,
            end=content_end,
            join_wrapped=True,
        )
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
                pane_left=_coerce_int(getattr(pane, "pane_left", None)),
                pane_top=_coerce_int(getattr(pane, "pane_top", None)),
                pane_right=_coerce_int(getattr(pane, "pane_right", None)),
                pane_bottom=_coerce_int(getattr(pane, "pane_bottom", None)),
                pane_at_left=_coerce_bool(getattr(pane, "pane_at_left", None)),
                pane_at_right=_coerce_bool(getattr(pane, "pane_at_right", None)),
                pane_at_top=_coerce_bool(getattr(pane, "pane_at_top", None)),
                pane_at_bottom=_coerce_bool(getattr(pane, "pane_at_bottom", None)),
                pane_tty=getattr(pane, "pane_tty", None),
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

    page_start = offset
    page_end: int | None = None if limit is None else page_start + limit
    page_matches = all_matches[page_start:page_end]

    skipped_panes = [m.pane_id for m in all_matches[page_start:][len(page_matches) :]]
    global_truncated = bool(skipped_panes)

    return SearchPanesResult(
        matches=page_matches,
        searched_scope=(
            "scrollback"
            if (content_start is not None or content_end is not None)
            else "visible"
        ),
        truncated=per_pane_truncated or global_truncated,
        truncated_panes=skipped_panes,
        total_panes_matched=total_panes_matched,
        offset=offset,
        limit=limit,
    )
