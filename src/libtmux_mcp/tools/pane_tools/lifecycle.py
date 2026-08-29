"""Pane lifecycle tools: kill, respawn, title, info."""

from __future__ import annotations

import pathlib
import shlex
import time
import typing as t

from libtmux import exc

if t.TYPE_CHECKING:
    from libtmux.pane import Pane

from libtmux_mcp._history import _prepare_spawn_environment
from libtmux_mcp._tmux_format import _escaped_or_none, escape_format
from libtmux_mcp._utils import (
    ExpectedToolError,
    _caller_is_on_server,
    _get_caller_identity,
    _get_server,
    _raise_if_shell_unrunnable,
    _raise_if_start_directory_unusable,
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
    window = pane.window
    window_id = window.window_id if window is not None else None
    session_id = window.session.session_id if window is not None else None
    session_name = (
        (window.session.session_name or session_id) if window is not None else None
    )
    pane.kill()
    # A window with no panes left is removed, and a session with no
    # windows left goes with it. The tier permits the cascade; the bare
    # "Pane killed" hid it.
    gone: list[str] = []
    if window_id and server.windows.get(window_id=window_id, default=None) is None:
        gone.append(f"window {window_id}")
    if session_id and server.sessions.get(session_id=session_id, default=None) is None:
        gone.append(f"session {session_name}")
    if gone:
        return f"Pane killed: {pid} (it was the last, so {' and '.join(gone)} went too)"
    return f"Pane killed: {pid}"


#: Ceiling for the pid to change. Exits the instant it does, so a generous
#: value is free -- and 0.25 was NOT generous: it failed at loadavg 30,
#: where the round trip outlasts the settle it was written to absorb.
_RESPAWN_PID_SECONDS = 5.0

#: Grace for the command to catch up ONCE THE PID HAS CHANGED. Small on
#: purpose: a command whose basename never appears -- ``env FOO=1 sleep
#: 5`` reports ``sleep``, not ``env`` -- pays this in full, so it is a
#: bill rather than a ceiling and cannot be widened the same way. On tmux
#: 3.7c over 15 runs, ``pane_current_command`` reached its new value by
#: 26.4 ms worst case.
_RESPAWN_COMMAND_SECONDS = 0.25
_RESPAWN_SETTLE_INTERVAL = 0.005


def _expected_command(shell: str | None) -> str | None:
    """Basename tmux will report for *shell*, if it can be predicted."""
    if not shell:
        return None
    try:
        program = shlex.split(shell)[0]
    except (ValueError, IndexError):
        return None
    return pathlib.PurePath(program).name or None


def _settle_respawned_pane(
    pane: Pane, shell: str | None, previous_pid: str | None = None
) -> None:
    """Wait until tmux reports the respawned pane's NEW command.

    ``pane_pid`` changes the instant ``respawn-pane`` returns, but
    ``pane_current_command`` lags it by a median of 14 ms while tmux's
    login shell is replaced. Serializing the first read describes the
    process about to be replaced -- which is why this project's own
    ``test_respawn_pane_replaces_shell`` has been failing intermittently
    since it was written, absorbed by ``--reruns=2``.

    Three plausible predicates were measured and all three fail:

    * **Wait for the pid to change.** Necessary but not sufficient --
      over 15 runs the command was still stale at pid-change 15 times.
    * **Wait for the command to change.** Never fires when a shell is
      respawned as itself, so it spins to the cap on the commonest
      respawn of all.
    * **Wait for two consecutive equal reads.** The worst of the three:
      the pre-change value is *stable* for those 14 ms, so a fast poll
      debounces onto the OLD value and returns it confidently. Measured
      against this very function: 0/6 stale at a 20 ms poll, 2/6 at
      5 ms, 3/6 at 1 ms -- correct only by accident of the interval.

    So: match the requested command's basename, which is the only
    predicate the data supports. Without a ``shell`` there is nothing to
    wait for, and that is structural rather than merely observed:
    ``spawn.c`` guards its default-command fallback on
    ``sc->argc == 0 && (~sc->flags & SPAWN_RESPAWN)``, so a commandless
    RESPAWN skips it and reuses the pane's existing ``argv``. The new
    command therefore equals the old one for any pane, including one
    running ``vim`` rather than a shell, and the immediate read is
    already right. A requested command whose basename never appears
    (a wrapper like ``env FOO=1 sleep 5`` reports ``sleep``, not ``env``)
    falls through to the cap, which still exceeds the measured settle
    time tenfold. Waiting is always safe; guessing is not.
    """
    expected = _expected_command(shell)
    if expected is None:
        return
    # Split in two so neither half has to be both generous and cheap.
    # A single budget could not be: widening it to survive load also
    # widened what a never-matching wrapper pays, every time.
    pid_deadline = time.monotonic() + _RESPAWN_PID_SECONDS
    while previous_pid is not None and time.monotonic() < pid_deadline:
        stdout = pane.display_message("#{pane_pid}", get_text=True)
        if stdout and stdout[0] != previous_pid:
            break
        time.sleep(_RESPAWN_SETTLE_INTERVAL)
    deadline = time.monotonic() + _RESPAWN_COMMAND_SECONDS
    while time.monotonic() < deadline:
        stdout = pane.display_message("#{pane_current_command}", get_text=True)
        if stdout and stdout[0] == expected:
            return
        time.sleep(_RESPAWN_SETTLE_INTERVAL)


@handle_tool_errors
def respawn_pane(
    pane_id: str,
    kill: bool = True,
    shell: str | None = None,
    start_directory: str | None = None,
    environment: dict[str, str] | str | None = None,
    socket_name: str | None = None,
    *,
    suppress_persistent_history: bool = False,
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
    environment : dict or str, optional
        Environment variables to set for the relaunched process. Each
        item becomes one ``-e KEY=VALUE`` flag (tmux's
        ``cmd-respawn-pane.c`` supports the flag repeatedly). Values
        supplied in a mapping are redacted in the audit log on a
        per-key basis — keys like ``DATABASE_URL`` remain visible but
        their values are replaced by ``{len, digest}`` digests.
        A JSON object string is redacted as one scalar digest, so its
        keys are not retained in the audit record. Values may still
        appear briefly in the OS process table while tmux spawns the
        new process; do not pass long-lived secrets here when a
        host-resident agent or other tenant could observe ``ps``.
    socket_name : str, optional
        tmux socket name.
    suppress_persistent_history : bool
        Whether to suppress persistent history for the spawned shell. Defaults
        to False for MCP and direct Python calls. This per-call option does not
        inherit LIBTMUX_SUPPRESS_HISTORY. Startup files may override these
        controls.

    Returns
    -------
    PaneInfo
        Serialized pane metadata after respawn. The pane_id is
        preserved; pane_pid reflects the new process.
    """
    spawn_environment = _prepare_spawn_environment(
        environment,
        suppress_persistent_history=suppress_persistent_history,
    )
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
    _raise_if_shell_unrunnable(
        shell,
        consequence=(
            "Respawning with it would kill the pane: tmux reports "
            "success, the new process exits immediately, and the pane "
            "goes with it."
        ),
    )
    _raise_if_start_directory_unusable(start_directory)
    previous = pane.display_message("#{pane_pid}", get_text=True)
    previous_pid = previous[0] if previous else None
    pane.respawn(
        kill=kill,
        start_directory=_escaped_or_none(start_directory),
        environment=spawn_environment,
        shell=shell,
    )
    # Pick up fresh pane_pid and any command/path updates; tmux does
    # not invalidate the underlying object on respawn.
    try:
        _settle_respawned_pane(pane, shell, previous_pid)
        pane.refresh()
    except exc.TmuxObjectDoesNotExist as err:
        # tmux does not fail a respawn whose command cannot be executed:
        # the new process dies at once and takes the pane with it -- and
        # the window, session and server too, if it was the last. The
        # stale object would describe a pane that no longer exists.
        msg = (
            f"pane {pane_id} did not survive the respawn: its new command "
            "exited immediately. The pane is gone, along with its window "
            "and session if it was the last one."
        )
        raise ExpectedToolError(msg) from err
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
    pane.set_title(escape_format(title))
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

    Use this for metadata (PID, path, dimensions) without reading terminal
    content. For content INSTEAD of metadata, use ``capture_pane``. For
    content AND metadata, use ``snapshot_pane`` -- it returns both in one
    call, and pairing this tool with ``capture_pane`` is the two-call
    pattern ``snapshot_pane`` exists to replace.

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
    ExpectedToolError
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

    # When several panes qualify -- a single-pane window touches all four
    # edges -- prefer the one whose top-left is furthest from the window
    # origin, so 'bottom-right' picks the largest pane_left + pane_top.
    def _innermost_score(p: t.Any) -> int:
        try:
            return int(p.pane_left or 0) + int(p.pane_top or 0)
        except (TypeError, ValueError):
            return 0

    matches.sort(key=_innermost_score, reverse=True)
    return _serialize_pane(matches[0])
