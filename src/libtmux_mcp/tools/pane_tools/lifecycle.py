"""Pane lifecycle tools: kill, respawn, title, info."""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _caller_is_on_server,
    _get_caller_identity,
    _get_server,
    _resolve_pane,
    _serialize_pane,
    handle_tool_errors,
)
from libtmux_mcp.models import (
    PaneInfo,
)


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
        raise ToolError(msg)

    pane = _resolve_pane(server, pane_id=pane_id)
    pid = pane.pane_id
    pane.kill()
    return f"Pane killed: {pid}"


@handle_tool_errors
def respawn_pane(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    kill: bool = True,
    shell: str | None = None,
    start_directory: str | None = None,
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
    the new process.

    ``pane_id`` is required — no fallback to ``_resolve_pane``'s
    "first pane in session/window" behaviour. Default ``kill=True``
    will terminate the resolved pane's process, so accidental targeting
    can silently kill an unrelated server. Resolve via ``list_panes``
    first.

    Tip: call ``get_pane_info`` first if you need to capture
    ``pane_current_command`` before respawn — the new process loses its
    argv. Omitting ``shell`` makes tmux replay the original argv (good
    default for shells; may differ for processes spawned via custom
    shell at split time).

    Parameters
    ----------
    pane_id : str
        Pane ID (e.g. '%1'). Required — no fallback resolution.
    session_name : str, optional
        Accepted for backwards-compatibility with the ``_resolve_pane``
        signature shared across pane tools, but the explicit ``pane_id``
        guard above raises before this is consulted.
    session_id : str, optional
        See ``session_name``.
    window_id : str, optional
        See ``session_name``.
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
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneInfo
        Serialized pane metadata after respawn. The pane_id is
        preserved; pane_pid reflects the new process.
    """
    if pane_id is None:
        msg = (
            "respawn_pane requires an explicit pane_id (e.g. '%1') because "
            "default kill=True will terminate the resolved pane's process. "
            "Resolve the target via list_panes first."
        )
        raise ToolError(msg)
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
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
        raise ToolError(msg)
    # Stopgap: ``libtmux>=0.55.1`` has no ``Pane.respawn()`` yet — the
    # wrapper exists on the upstream ``tmux-parity`` branch (see
    # ``libtmux/pane.py:respawn``) and mirrors this argv shape (``-k``,
    # ``-c <dir>``, optional trailing shell). When the release line picks
    # it up, swap ``pane.cmd("respawn-pane", *argv)`` for ``pane.respawn(
    # kill=kill, start_directory=start_directory, shell=shell)`` and drop
    # the stderr branch — ``Pane.respawn`` raises ``LibTmuxException``.
    argv: list[str] = []
    if kill:
        argv.append("-k")
    if start_directory is not None:
        argv.extend(["-c", start_directory])
    if shell is not None:
        argv.append(shell)
    result = pane.cmd("respawn-pane", *argv)
    if result.stderr:
        stderr = " ".join(result.stderr).strip()
        msg = f"tmux respawn-pane failed: {stderr}"
        raise ToolError(msg)
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
