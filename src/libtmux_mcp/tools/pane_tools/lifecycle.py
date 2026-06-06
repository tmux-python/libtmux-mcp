"""Pane lifecycle tools: kill, respawn, title, info."""

from __future__ import annotations

import typing as t

from libtmux_mcp._utils import (
    ExpectedToolError,
    _caller_is_on_server,
    _get_caller_identity,
    _get_server,
    _resolve_pane,
    _resolve_window,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneInfo,
)

#: The four window corners ``find_pane_by_position`` accepts.
PaneCorner = t.Literal["top-left", "top-right", "bottom-left", "bottom-right"]


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
    server = _get_server(socket_name=socket_name)
    caller = _get_caller_identity()
    if (
        caller is not None
        and caller.pane_id == pane_id
        and _caller_is_on_server(server, caller)
    ):
        msg = (
            "Refusing to kill the pane running this MCP server. "
            "Use a manual tmux command if intended."
        )
        raise ExpectedToolError(msg)

    pane = _resolve_pane(server, pane_id=pane_id)
    pid = pane.pane_id
    pane.kill()
    return f"Pane killed: {pid}"


@handle_tool_errors
def respawn_pane(
    pane_id: str,
    kill: bool = True,
    shell: str | None = None,
    start_directory: str | None = None,
    environment: dict[str, str] | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Restart a pane's process in place, preserving pane_id and layout.

    Use when a shell wedges (hung REPL, runaway process, bad terminal
    mode). The alternative — kill_pane + split_window — destroys
    pane_id references the agent may still be holding, and rearranges
    the layout. respawn-pane preserves both.

    With ``kill=True`` (the default), tmux kills the existing process
    before respawning. Optional ``shell`` replaces the command tmux
    relaunches; ``start_directory`` sets the working directory for
    the new process; ``environment`` sets per-process environment
    variables for the relaunched command (one ``-e KEY=VALUE`` flag
    per entry).

    ``pane_id`` is required — sibling pane tools accept a hierarchical
    fallback (``session_name`` / ``window_id`` / ``pane_index``) that
    resolves to "first pane in session/window", but combined with
    default ``kill=True`` that fallback could silently kill an
    unrelated process. The signature deliberately omits the resolver
    fields so the FastMCP schema rejects them at the framework
    boundary. Resolve via ``list_panes`` first.

    Tip: call ``get_pane_info`` first if you need to capture
    ``pane_current_command`` before respawn — the new process loses its
    argv. Omitting ``shell`` makes tmux replay the original argv (good
    default for shells; may differ for processes spawned via custom
    shell at split time).

    Parameters
    ----------
    pane_id : str
        Pane ID (e.g. '%1'). Required.
    kill : bool
        When True (default), pass ``-k`` to tmux so the current
        process is killed before respawning. When False, respawn
        fails if the pane already has a running process.
    shell : str, optional
        Replacement command for tmux to launch. When omitted, tmux
        replays the original argv (good default for shells; may differ
        for processes spawned via custom shell at split time). Matches
        the ``shell`` parameter on :func:`split_window` and the
        eventual upstream ``Pane.respawn(shell=)`` API.
    start_directory : str, optional
        Working directory for the relaunched command (maps to
        ``respawn-pane -c``).
    environment : dict[str, str], optional
        Environment variables to set for the relaunched process. Each
        item becomes one ``-e KEY=VALUE`` flag (tmux's
        ``cmd-respawn-pane.c`` supports the flag repeatedly). Values
        are redacted in the audit log on a per-key basis — keys like
        ``DATABASE_URL`` remain visible but their values are replaced
        by ``{len, sha256_prefix}`` digests. Note that the values may
        still appear briefly in the OS process table while tmux spawns
        the new process; do not pass long-lived secrets here when a
        host-resident agent or other tenant could observe ``ps``.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane metadata after respawn. The pane_id is
        preserved; pane_pid reflects the new process.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(server, pane_id=pane_id)
    caller = _get_caller_identity()
    if (
        caller is not None
        and caller.pane_id == pane.pane_id
        and _caller_is_on_server(server, caller)
    ):
        msg = (
            "Refusing to respawn the pane running this MCP server. "
            "Use a manual tmux command if intended."
        )
        raise ExpectedToolError(msg)
    pane.respawn(
        kill=kill,
        start_directory=start_directory,
        environment=environment,
        shell=shell,
    )
    # Pick up fresh pane_pid and any command/path updates; tmux does
    # not invalidate the underlying object on respawn.
    pane.refresh()
    return _serialize_pane(pane)


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
def find_pane_by_position(
    corner: PaneCorner,
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> PaneInfo:
    """Find the pane occupying a corner of a tmux window.

    Composes the four ``pane_at_*`` predicates so callers can target a
    layout-relative position (e.g. "the bottom-right pane") in one
    round-trip instead of listing every pane and computing the
    geometry. Resolves the window the same way as the other
    window-scoped tools.

    Parameters
    ----------
    corner : str
        One of ``'top-left'``, ``'top-right'``, ``'bottom-left'``,
        ``'bottom-right'``.
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index. Requires session_name or session_id.
    session_name : str, optional
        Session name.
    session_id : str, optional
        Session ID.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane occupying the requested corner.

    Raises
    ------
    ToolError
        If no pane satisfies both edge predicates for that corner — in
        practice only possible for layouts tmux itself produced via
        custom layout strings; the built-in layouts always have a pane
        at every corner.
    """
    server = _get_server(socket_name=socket_name)
    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )

    vertical, horizontal = corner.split("-")
    matches = [
        p
        for p in window.panes
        if getattr(p, f"at_{vertical}", False) and getattr(p, f"at_{horizontal}", False)
    ]
    if not matches:
        msg = (
            f"No pane found at corner {corner!r} in window "
            f"{window.window_id}. This is unusual — built-in layouts "
            "always have a pane at every corner."
        )
        raise ExpectedToolError(msg)

    # When more than one pane qualifies (e.g. a single-pane window
    # touches all four edges, or an unusual layout), prefer the pane
    # whose top-left coordinate is furthest from window origin (0,0).
    # That picks the visually innermost pane for the corner — i.e.
    # for 'bottom-right', the pane with the largest pane_left +
    # pane_top, which sits visually closest to the bottom-right.
    def _innermost_score(p: t.Any) -> int:
        try:
            return int(p.pane_left or 0) + int(p.pane_top or 0)
        except (TypeError, ValueError):
            return 0

    matches.sort(key=_innermost_score, reverse=True)
    return _serialize_pane(matches[0])
