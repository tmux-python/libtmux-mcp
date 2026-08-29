"""MCP tools for tmux window operations."""

from __future__ import annotations

import typing as t

from libtmux import exc
from libtmux.constants import PaneDirection
from libtmux.pane import Pane
from libtmux.window import Window

from libtmux_mcp._history import _prepare_spawn_environment
from libtmux_mcp._utils import (
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    DISCOVERY_META,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    ExpectedToolError,
    _apply_filters,
    _caller_is_on_server,
    _get_caller_identity,
    _get_server,
    _raise_if_shell_unrunnable,
    _raise_if_spawned_pane_is_gone,
    _raise_if_start_directory_unusable,
    _raise_spawned_pane_gone,
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    _serialize_pane,
    _serialize_window,
    handle_tool_errors,
)
from libtmux_mcp.models import PaneInfo, PaneMoveResult, SplitResult, WindowInfo

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

_DIRECTION_MAP: dict[str, PaneDirection] = {
    "above": PaneDirection.Above,
    "below": PaneDirection.Below,
    "right": PaneDirection.Right,
    "left": PaneDirection.Left,
}


@handle_tool_errors
def list_panes(
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    window_index: str | None = None,
    socket_name: str | None = None,
    filters: dict[str, str] | str | None = None,
) -> list[PaneInfo]:
    """List tmux panes (terminal multiplexer splits) in a window, session, or server.

    Use for terminal panes — including 'this pane', 'current pane',
    'split pane', 'the bottom shell' — not editor splits or browser panes.
    Only searches pane metadata (current command, title, working directory);
    to search the actual visible terminal text, use search_panes.

    Parameters
    ----------
    session_name : str, optional
        Session name. If given without window params, lists all panes
        in the session.
    session_id : str, optional
        Session ID. If given without window params, lists all panes
        in the session.
    window_id : str, optional
        Window ID (e.g. '@1'). Scopes to a single window.
    window_index : str, optional
        Window index within the session. Scopes to a single window.
    socket_name : str, optional
        tmux socket name.
    filters : dict or str, optional
        Django-style filters as a dict
        (e.g. ``{"pane_current_command__contains": "vim"}``)
        or as a JSON string. Some MCP clients require the string form.
        Every field this tool returns is filterable, including
        is_caller -- filter ``{"is_caller": true}`` to answer
        "which pane am I in?". Any libtmux Pane attribute works too.

    Returns
    -------
    list[PaneInfo]
        List of serialized pane objects.
    """
    server = _get_server(socket_name=socket_name)
    if window_id is not None or window_index is not None:
        window = _resolve_window(
            server,
            window_id=window_id,
            window_index=window_index,
            session_name=session_name,
            session_id=session_id,
        )
        panes = window.panes
    elif session_name is not None or session_id is not None:
        session = _resolve_session(
            server, session_name=session_name, session_id=session_id
        )
        panes = session.panes
    else:
        panes = server.panes
    return _apply_filters(panes, filters, _serialize_pane, Pane, PaneInfo)


# get_window_info completes the core-tmux-hierarchy symmetry of get_*_info
# tools: the four hierarchy levels (server, session, window, pane) now each
# have a targeted single-object read. This is deliberately NOT a license to
# add get_buffer_info / get_hook_info / get_option_info — those scopes are
# not part of the hierarchy and the existing show_*/load_* tools already
# cover their reads.
@handle_tool_errors
def get_window_info(
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> WindowInfo:
    """Return metadata for a single tmux window (ID, name, layout, dimensions).

    Use this instead of list_windows + filter when you only need one
    window's info. Resolves the window by window_id first; falls back
    to window_index within a session if window_id is not given.

    Parameters
    ----------
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index within the session. Requires session_name or
        session_id to disambiguate.
    session_name : str, optional
        Session name for window_index lookup.
    session_id : str, optional
        Session ID for window_index lookup.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WindowInfo
        Serialized window metadata.
    """
    server = _get_server(socket_name=socket_name)
    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )
    return _serialize_window(window)


#: A split takes ``size`` for the NEW pane and one column or row for the
#: border, so the pane being split keeps ``extent - size - 1``. The line
#: is drawn where tmux stops HONOURING the request rather than where the
#: layout gets cramped: leaving the source at one column is a choice, and
#: refusing it would be policing the caller's layout. Being told the new
#: pane is 78 when 120 was asked for is a false report.
_MIN_REMAINING_EXTENT = 1


def _raise_if_size_would_flatten_the_source(
    target: Pane | Window, direction: PaneDirection | None, size: str | int | None
) -> None:
    """Refuse a split that would leave the pane being split unusable.

    ``size`` names the NEW pane, the way tmux's ``-l`` does. Measured on
    an 80-column pane: 78 is the largest value tmux honours (78 + border
    + 1 for the source), and every larger value -- 79, 80, 120,
    1_000_000 -- silently CLAMPS the new pane to 78 while leaving the
    source at one column.

    Those clamped values are what this refuses. The result covers only
    the new pane, so a caller who asked for 120 was told 78 and shown
    nothing about their own pane being flattened -- a clean success
    report for a broken layout, which is worse than tmux's silence
    because it is actively reassuring.

    A faithful split that happens to leave a narrow source is allowed.
    That is the caller's layout to choose; the report is true.
    """
    if size is None:
        return
    source = target if isinstance(target, Pane) else target.active_pane
    if source is None:
        return
    vertical = direction in (PaneDirection.Above, PaneDirection.Below, None)
    raw = source.pane_height if vertical else source.pane_width
    try:
        extent = int(raw or 0)
    except ValueError:
        return
    if extent <= 0:
        return

    if isinstance(size, str) and size.endswith("%"):
        try:
            requested = extent * int(size[:-1]) // 100
        except ValueError:
            return  # tmux will reject the spelling itself
    else:
        try:
            requested = int(size)
        except (TypeError, ValueError):
            return

    largest = extent - _MIN_REMAINING_EXTENT - 1
    if requested <= largest:
        return
    axis = "rows" if vertical else "columns"
    msg = (
        f"size={size!r} leaves the pane being split with "
        f"{max(extent - requested - 1, 0)} {axis}. size names the NEW pane "
        f"and one {axis[:-1]} goes to the border, so the source keeps "
        f"extent - size - 1: at {extent} {axis} the largest usable size is "
        f"{largest}. tmux reports none of this -- it clamps the new pane and "
        "leaves the source as a sliver."
    )
    raise ExpectedToolError(msg)


@handle_tool_errors
def split_window(
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    window_index: str | None = None,
    direction: t.Literal["above", "below", "left", "right"] | None = None,
    size: str | int | None = None,
    start_directory: str | None = None,
    shell: str | None = None,
    socket_name: str | None = None,
    *,
    environment: dict[str, str] | str | None = None,
    suppress_persistent_history: bool = False,
) -> SplitResult:
    """Split a tmux window to create a new pane.

    Creates a new pane by splitting an existing one. Use direction to choose
    above/below/left/right. Returns the new pane's info including its pane_id.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID to split from. If given, splits adjacent to this pane.
    session_name : str, optional
        Session name.
    session_id : str, optional
        Session ID (e.g. '$1').
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index within the session.
    direction : str, optional
        Split direction.
    size : str or int, optional
        Size of the NEW pane, as tmux's ``-l`` means it -- not the pane
        being split, which keeps ``extent - size - 1`` after the border.
        Use a string with '%%' suffix for percentage (e.g. '50%%') or an
        integer for lines/columns. A size tmux would silently clamp is
        refused, naming the largest that fits.
    start_directory : str, optional
        Working directory for the new pane.
    shell : str, optional
        Shell command to run in the new pane.
    socket_name : str, optional
        tmux socket name.
    environment : dict or str, optional
        Per-process environment as a mapping or JSON object string. Values do
        not modify the tmux session environment. Each item becomes a tmux
        ``-e`` launch option. Values may be visible to host process inspection
        in the tmux client argv during launch and in the child environment
        afterward; MCP audit redaction does not hide either surface. Pass
        credential references, not literal credentials.
    suppress_persistent_history : bool
        Whether to suppress persistent history for the spawned shell. Defaults
        to False for MCP and direct Python calls. This per-call option does not
        inherit LIBTMUX_SUPPRESS_HISTORY. Startup files may override these
        controls.

    Returns
    -------
    SplitResult
        The new pane, with every ``PaneInfo`` field where it was, plus
        ``source_pane``: the pane that was split, as it stands after
        the split. That extent is what constrains the next split of the
        same region.
    """
    _raise_if_shell_unrunnable(
        shell,
        consequence=(
            "Splitting with it would report a new pane that no longer "
            "exists: tmux reports success, the new process exits "
            "immediately, and the pane goes with it."
        ),
    )
    _raise_if_start_directory_unusable(start_directory)
    spawn_environment = _prepare_spawn_environment(
        environment,
        suppress_persistent_history=suppress_persistent_history,
    )
    server = _get_server(socket_name=socket_name)

    pane_dir: PaneDirection | None = None
    if direction is not None:
        pane_dir = _DIRECTION_MAP.get(direction)
        if pane_dir is None:
            valid = ", ".join(sorted(_DIRECTION_MAP))
            msg = f"Invalid direction: {direction!r}. Valid: {valid}"
            raise ExpectedToolError(msg)

    target: Pane | Window
    if pane_id is not None:
        target = _resolve_pane(server, pane_id=pane_id)
    else:
        target = _resolve_window(
            server,
            window_id=window_id,
            window_index=window_index,
            session_name=session_name,
            session_id=session_id,
        )
    # A command that cannot run leaves tmux reporting success with no
    # pane behind it. The window path notices while building the object
    # and raises a bare "Could not find pane_id"; the pane path returns
    # a stale object that only fails on the caller's NEXT call.
    _raise_if_size_would_flatten_the_source(target, pane_dir, size)
    try:
        new_pane = target.split(
            direction=pane_dir,
            size=size,
            start_directory=start_directory,
            shell=shell,
            environment=spawn_environment,
        )
    except exc.TmuxObjectDoesNotExist:
        _raise_spawned_pane_gone(shell)
    _raise_if_spawned_pane_is_gone(new_pane, shell)
    source = target if isinstance(target, Pane) else target.active_pane
    source_info: PaneInfo | None = None
    if source is not None:
        # Re-read: the in-hand object still describes the pre-split
        # geometry, which is the number the caller must NOT plan with.
        try:
            source.refresh()
            source_info = _serialize_pane(source)
        except exc.TmuxObjectDoesNotExist:
            source_info = None
    return SplitResult(
        **_serialize_pane(new_pane).model_dump(), source_pane=source_info
    )


@handle_tool_errors
def rename_window(
    new_name: str,
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> WindowInfo:
    """Rename a tmux window.

    Use when a window's purpose has changed. Existing window_id references
    remain valid after renaming.

    Parameters
    ----------
    new_name : str
        The new name for the window.
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index within the session.
    session_name : str, optional
        Session name.
    session_id : str, optional
        Session ID.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WindowInfo
        Serialized window object.
    """
    server = _get_server(socket_name=socket_name)
    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )
    window.rename_window(new_name)
    return _serialize_window(window)


@handle_tool_errors
def kill_window(
    window_id: str,
    socket_name: str | None = None,
) -> str:
    """Kill (close) a tmux window. Requires exact window_id (e.g. '@3').

    Destroys the window and all its panes. Use kill_pane to remove a single
    pane instead. Self-kill protection prevents killing the window containing
    this MCP process.

    Parameters
    ----------
    window_id : str
        Window ID (e.g. '@1'). Required — no fallback resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    server = _get_server(socket_name=socket_name)
    window = _resolve_window(server, window_id=window_id)

    caller = _get_caller_identity()
    if _caller_is_on_server(server, caller) and caller is not None and caller.pane_id:
        caller_pane = server.panes.get(pane_id=caller.pane_id, default=None)
        if caller_pane is not None and caller_pane.window_id == window_id:
            msg = (
                "Refusing to kill the window containing this MCP server's pane. "
                "Use a manual tmux command if intended."
            )
            raise ExpectedToolError(msg)

    wid = window.window_id
    session = window.session
    session_id = session.session_id
    session_name = session.session_name or session_id
    window.kill()
    # Killing a session's LAST window destroys the session. Reported
    # rather than left to be discovered: the tier permits it, but an
    # agent tidying up a window has no reason to expect it, and the
    # bare "Window killed" understates the blast radius.
    if server.sessions.get(session_id=session_id, default=None) is None:
        return (
            f"Window killed: {wid} (session {session_name} was its last, and is gone)"
        )
    return f"Window killed: {wid}"


@handle_tool_errors
def select_layout(
    layout: str,
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> WindowInfo:
    """Set the layout of a tmux window.

    Choose from: even-horizontal, even-vertical, main-horizontal,
    main-vertical, or tiled. Rearranges all panes in the window.

    Parameters
    ----------
    layout : str
        Layout name or custom layout string. Built-in layouts:
        'even-horizontal', 'even-vertical', 'main-horizontal',
        'main-horizontal-mirrored', 'main-vertical',
        'main-vertical-mirrored', 'tiled'.
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index within the session.
    session_name : str, optional
        Session name.
    session_id : str, optional
        Session ID.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WindowInfo
        Serialized window object.
    """
    server = _get_server(socket_name=socket_name)
    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )
    window.select_layout(layout)
    return _serialize_window(window)


@handle_tool_errors
def resize_window(
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    height: int | None = None,
    width: int | None = None,
    socket_name: str | None = None,
) -> WindowInfo:
    """Resize a tmux window.

    Use to adjust the window dimensions. This affects all panes within the window.

    Parameters
    ----------
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index within the session.
    session_name : str, optional
        Session name.
    session_id : str, optional
        Session ID.
    height : int, optional
        New height in lines.
    width : int, optional
        New width in columns.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WindowInfo
        Serialized window object.
    """
    server = _get_server(socket_name=socket_name)
    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )
    window.resize(height=height, width=width)
    return _serialize_window(window)


@handle_tool_errors
def move_window(
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    destination_index: str = "",
    destination_session: str | None = None,
    socket_name: str | None = None,
) -> WindowInfo:
    """Move a window to a different index or session.

    Reorder windows within a session or move a window to another session.

    Parameters
    ----------
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index within the session.
    session_name : str, optional
        Source session name.
    session_id : str, optional
        Source session ID.
    destination_index : str
        Target window index. Default empty string (next available).
    destination_session : str, optional
        Target session name or ID. Default is current session.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WindowInfo
        Serialized window after move.
    """
    server = _get_server(socket_name=socket_name)
    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )
    # Moving a session's LAST window to another session leaves the
    # source with none, and tmux destroys it -- measured, moving alpha's
    # only window to beta made alpha cease to exist while the result
    # named only the destination. Same shape as break_pane, and
    # avoidable the same way, so refused rather than disclosed:
    # destroying a session is destructive-tier work.
    if destination_session is not None and len(window.session.windows) == 1:
        msg = (
            f"window {window.window_id} is the only window in session "
            f"{window.session.session_name!r}, so moving it to another "
            "session would leave that one empty and tmux would destroy it. "
            "Create another window there first."
        )
        raise ExpectedToolError(msg)

    window.move_window(
        destination=destination_index,
        session=destination_session,
    )
    # libtmux's Window.move_window skips its own refresh when BOTH a
    # non-empty destination index and a target session are passed — in
    # that branch session_id stays stale. Refresh unconditionally so
    # _serialize_window always reads fresh metadata.
    window.refresh()
    return _serialize_window(window)


@handle_tool_errors
def break_pane(
    pane_id: str,
    window_name: str | None = None,
    socket_name: str | None = None,
) -> WindowInfo:
    """Move a pane out into a window of its own.

    Keeps the pane and everything running in it. The alternative --
    killing it and starting again elsewhere -- loses the process, the
    scrollback and the pane id, and any cursor a caller is holding
    against that id.

    The new window may land in a DIFFERENT SESSION. tmux puts it in the
    current session, and "current" is the most recently active one, not
    the pane's own -- so breaking a pane out of session A can move it to
    session B. Measured. The returned ``session_name`` and
    ``session_id`` are read back afterwards and say where it went, but
    check them: this is a mutation that has already crossed a session
    boundary by the time it reports.

    Parameters
    ----------
    pane_id : str
        Pane to break out (e.g. '%1').
    window_name : str, optional
        Name for the new window. Defaults to tmux's choice.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    WindowInfo
        The window the pane now lives in, re-read after the move rather
        than assumed, so the reported id is the one that exists.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(server, pane_id=pane_id)

    # tmux puts the new window in the CURRENT session, which is not
    # necessarily the pane's own. If the pane is the last one in its
    # session's last window, the source session is left with no windows
    # and tmux destroys it -- measured: breaking alpha's only pane moved
    # it to beta and alpha ceased to exist, while the result reported
    # only where the pane went.
    #
    # Destroying a session is destructive-tier work and this tool is
    # mutating, so refuse rather than disclose.
    source_window = pane.window
    if (
        source_window is not None
        and len(source_window.panes) == 1
        and len(source_window.session.windows) == 1
    ):
        msg = (
            f"pane {pane_id} is the only pane in the only window of session "
            f"{source_window.session.session_name!r}, so breaking it out would "
            "leave that session with no windows and tmux would destroy it. "
            "Create another window in that session first, or move the pane "
            "with join_pane, which never empties its source."
        )
        raise ExpectedToolError(msg)

    pane.break_pane(window_name=window_name)
    moved = server.panes.get(pane_id=pane.pane_id, default=None)
    if moved is None or moved.window is None:
        msg = f"pane {pane_id} did not survive break-pane"
        raise ExpectedToolError(msg)
    return _serialize_window(moved.window)


@handle_tool_errors
def join_pane(
    pane_id: str,
    target_window_id: str,
    vertical: bool = True,
    socket_name: str | None = None,
) -> PaneMoveResult:
    """Move an existing pane into another window, splitting it.

    The counterpart to :func:`break_pane`, and the reason to prefer both
    over kill-and-recreate: the process, the scrollback and the pane id
    all survive the move.

    Parameters
    ----------
    pane_id : str
        Pane to move (e.g. '%1').
    target_window_id : str
        Window to move it into (e.g. '@2').
    vertical : bool
        Split the target vertically (stacked). False splits it
        horizontally (side by side).
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    PaneMoveResult
        The pane after the move, re-read so ``window_id`` reflects where
        it actually landed, plus whether the source window was DESTROYED
        by the move. tmux removes a window left with no panes, so
        consolidating panes deletes windows the caller never named.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(server, pane_id=pane_id)
    window = _resolve_window(server, window_id=target_window_id)
    source_window = pane.window
    source_window_id = source_window.window_id if source_window else None

    # A window emptying is inherent to moving its last pane, and is
    # disclosed below. A SESSION emptying is not inherent -- the caller
    # can add a window first -- and destroying a session is
    # destructive-tier work reachable from a mutating-tier client.
    # `break_pane` already refuses exactly this predicate; its sibling
    # reached the same end state with no check, and from there a client
    # restricted to `mutating` could take the whole server down.
    if (
        source_window is not None
        and len(source_window.panes) == 1
        and len(source_window.session.windows) == 1
        and source_window.session.session_id != window.session.session_id
    ):
        msg = (
            f"pane {pane_id} is the only pane in the only window of session "
            f"{source_window.session.session_name!r}, so moving it away would "
            "leave that session with no windows and tmux would destroy it. "
            "Create another window in that session first."
        )
        raise ExpectedToolError(msg)

    pane.join(window, vertical=vertical)

    moved = server.panes.get(pane_id=pane.pane_id, default=None)
    if moved is None:
        msg = f"pane {pane_id} did not survive join-pane"
        raise ExpectedToolError(msg)
    # Asked afterwards rather than predicted from the pane count: the
    # question is whether the window is still there, and that is
    # observable.
    destroyed = source_window_id is not None and (
        server.windows.get(window_id=source_window_id, default=None) is None
    )
    return PaneMoveResult(
        pane=_serialize_pane(moved),
        source_window_id=source_window_id,
        source_window_destroyed=destroyed,
    )


def register(mcp: FastMCP) -> None:
    """Register window-level tools with the MCP instance."""
    mcp.tool(
        title="List tmux Panes",
        annotations=ANNOTATIONS_RO,
        tags={TAG_READONLY},
        meta=DISCOVERY_META,
    )(list_panes)
    mcp.tool(
        title="Get tmux Window Info", annotations=ANNOTATIONS_RO, tags={TAG_READONLY}
    )(get_window_info)
    mcp.tool(
        title="Break Pane Into Window",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(break_pane)
    mcp.tool(
        title="Join Pane Into Window",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(join_pane)
    mcp.tool(
        title="Split tmux Window", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING}
    )(split_window)
    mcp.tool(
        title="Rename tmux Window",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(rename_window)
    mcp.tool(
        title="Kill tmux Window",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(kill_window)
    mcp.tool(
        title="Select tmux Layout",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(select_layout)
    mcp.tool(
        title="Resize tmux Window",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(resize_window)
    mcp.tool(
        title="Move tmux Window",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(move_window)
