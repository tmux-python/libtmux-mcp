"""MCP tools for tmux window operations."""

from __future__ import annotations

import typing as t

from libtmux.constants import PaneDirection
from libtmux.pane import Pane

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
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    _serialize_pane,
    _serialize_window,
    handle_tool_errors,
)
from libtmux_mcp.models import PaneInfo, WindowInfo

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
) -> PaneInfo:
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
        Size of the new pane. Use a string with '%%' suffix for
        percentage (e.g. '50%%') or an integer for lines/columns.
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
    PaneInfo
        Serialized pane object.
    """
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

    if pane_id is not None:
        pane = _resolve_pane(server, pane_id=pane_id)
        new_pane = pane.split(
            direction=pane_dir,
            size=size,
            start_directory=start_directory,
            shell=shell,
            environment=spawn_environment,
        )
    else:
        window = _resolve_window(
            server,
            window_id=window_id,
            window_index=window_index,
            session_name=session_name,
            session_id=session_id,
        )
        new_pane = window.split(
            direction=pane_dir,
            size=size,
            start_directory=start_directory,
            shell=shell,
            environment=spawn_environment,
        )
    return _serialize_pane(new_pane)


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
    window.kill()
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
) -> PaneInfo:
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
    PaneInfo
        The pane after the move, re-read so ``window_id`` reflects where
        it actually landed.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(server, pane_id=pane_id)
    window = _resolve_window(server, window_id=target_window_id)
    pane.join(window, vertical=vertical)
    moved = server.panes.get(pane_id=pane.pane_id, default=None)
    if moved is None:
        msg = f"pane {pane_id} did not survive join-pane"
        raise ExpectedToolError(msg)
    return _serialize_pane(moved)


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
