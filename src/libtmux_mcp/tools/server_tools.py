"""MCP tools for tmux server operations."""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import socket
import typing as t

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_RO,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    _apply_filters,
    _caller_is_on_server,
    _coerce_dict_arg,
    _get_caller_identity,
    _get_server,
    _invalidate_server,
    _serialize_session,
    handle_tool_errors,
)
from libtmux_mcp.models import ServerInfo, SessionInfo

logger = logging.getLogger(__name__)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


@handle_tool_errors
def list_sessions(
    socket_name: str | None = None,
    filters: dict[str, str] | str | None = None,
) -> list[SessionInfo]:
    """List all tmux sessions.

    Use as the starting point for discovery — call this before targeting
    specific sessions, windows, or panes.

    Parameters
    ----------
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.
    filters : dict or str, optional
        Django-style filters as a dict (e.g. ``{"session_name__contains": "dev"}``)
        or as a JSON string. Some MCP clients require the string form.

    Returns
    -------
    list[SessionInfo]
        List of session objects.
    """
    server = _get_server(socket_name=socket_name)
    sessions = server.sessions
    return _apply_filters(sessions, filters, _serialize_session)


@handle_tool_errors
def create_session(
    session_name: str | None = None,
    window_name: str | None = None,
    start_directory: str | None = None,
    x: int | None = None,
    y: int | None = None,
    environment: dict[str, str] | str | None = None,
    socket_name: str | None = None,
) -> SessionInfo:
    """Create a new tmux session.

    Check list_sessions first to avoid name conflicts. A new session
    starts with one window and one pane.

    Parameters
    ----------
    session_name : str, optional
        Name for the new session.
    window_name : str, optional
        Name for the initial window.
    start_directory : str, optional
        Working directory for the session.
    x : int, optional
        Width of the initial window.
    y : int, optional
        Height of the initial window.
    environment : dict or str, optional
        Environment variables to set. Accepts either a dict of env
        vars or a JSON-serialized string of the same — the latter is
        the cursor-composer-1 workaround described in
        :func:`libtmux_mcp._utils._coerce_dict_arg`.
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.

    Returns
    -------
    SessionInfo
        The created session.
    """
    server = _get_server(socket_name=socket_name)
    kwargs: dict[str, t.Any] = {}
    if session_name is not None:
        kwargs["session_name"] = session_name
    if window_name is not None:
        kwargs["window_name"] = window_name
    if start_directory is not None:
        kwargs["start_directory"] = start_directory
    if x is not None:
        kwargs["x"] = x
    if y is not None:
        kwargs["y"] = y
    coerced_env = _coerce_dict_arg("environment", environment)
    if coerced_env is not None:
        kwargs["environment"] = coerced_env
    session = server.new_session(**kwargs)
    return _serialize_session(session)


@handle_tool_errors
def kill_server(socket_name: str | None = None) -> str:
    """Kill the tmux server and all its sessions.

    Destroys ALL sessions, windows, and panes on this server. Use kill_session
    to remove a single session instead. Self-kill protection prevents killing
    the server running this MCP process.

    Parameters
    ----------
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.

    Returns
    -------
    str
        Confirmation message.
    """
    server = _get_server(socket_name=socket_name)

    caller = _get_caller_identity()
    if _caller_is_on_server(server, caller):
        msg = (
            "Refusing to kill the tmux server while this MCP server is running "
            "inside it. Use a manual tmux command if intended."
        )
        raise ToolError(msg)

    server.kill()
    _invalidate_server(socket_name=socket_name)
    return "Server killed successfully"


@handle_tool_errors
def get_server_info(socket_name: str | None = None) -> ServerInfo:
    """Get information about the tmux server.

    Use to verify the tmux server is running before other operations.
    For session-level details, use list_sessions instead.

    Parameters
    ----------
    socket_name : str, optional
        tmux socket name. Defaults to LIBTMUX_SOCKET env var.

    Returns
    -------
    ServerInfo
        Server information.
    """
    server = _get_server(socket_name=socket_name)
    alive = server.is_alive()
    version: str | None = None
    try:
        result = server.cmd("display-message", "-p", "#{version}")
        version = result.stdout[0] if result.stdout else None
    except Exception as err:
        # Best-effort — tmux ancient versions lack ``#{version}``,
        # permissions may deny display-message, etc. Mirrors the same
        # logging style used by ``_probe_server_by_path`` so operators
        # see a uniform signal when custom sockets fail to report
        # metadata.
        logger.debug("get_server_info: version query raised %s", err)
    return ServerInfo(
        is_alive=alive,
        socket_name=server.socket_name,
        socket_path=str(server.socket_path) if server.socket_path else None,
        session_count=len(server.sessions) if alive else 0,
        version=version,
    )


def _is_tmux_socket_live(path: pathlib.Path) -> bool:
    """Return True if a tmux socket has a listener accepting connections.

    Uses a UNIX-domain ``connect()`` with a short timeout rather than
    shelling out to ``tmux``. ``$TMUX_TMPDIR/tmux-$UID/`` routinely
    accumulates thousands of stale socket inodes from past servers —
    probing each one with ``tmux -L <name> ls`` would make
    :func:`list_servers` O(sockets * tmux-spawn-cost), easily tens of
    seconds on well-aged machines. Socket connect is kernel-fast (sub
    millisecond) and returns ``ECONNREFUSED`` immediately for dead
    inodes.
    """
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(0.1)
    try:
        s.connect(str(path))
    except (OSError, TimeoutError):
        return False
    else:
        return True
    finally:
        with contextlib.suppress(OSError):
            s.close()


def _probe_server_by_path(socket_path: pathlib.Path) -> ServerInfo | None:
    """Return a :class:`ServerInfo` for a live socket at ``socket_path``.

    Mirrors :func:`get_server_info`'s serialization but keys the Server
    by ``socket_path`` (-S) rather than socket name (-L) so callers can
    probe arbitrary ``tmux -S /path/...`` daemons that live outside
    ``$TMUX_TMPDIR``. Returns ``None`` when the path is not a socket,
    has no listener, or the server cannot be queried. Probe failures
    are logged at debug level so operators can surface "why isn't my
    custom socket appearing?" via verbose logging.
    """
    try:
        if not socket_path.is_socket():
            return None
    except OSError as err:
        logger.debug("probe %s: is_socket raised %s", socket_path, err)
        return None
    if not _is_tmux_socket_live(socket_path):
        return None
    server = _get_server(socket_path=str(socket_path))
    try:
        alive = server.is_alive()
    except Exception as err:
        logger.debug("probe %s: is_alive raised %s", socket_path, err)
        return None
    version: str | None = None
    try:
        result = server.cmd("display-message", "-p", "#{version}")
        version = result.stdout[0] if result.stdout else None
    except Exception as err:
        logger.debug("probe %s: version query raised %s", socket_path, err)
    return ServerInfo(
        is_alive=alive,
        socket_name=server.socket_name,
        socket_path=str(socket_path),
        session_count=len(server.sessions) if alive else 0,
        version=version,
    )


#: Tools that intentionally do NOT accept ``socket_name`` because they
#: discover or enumerate sockets themselves rather than connecting to a
#: known one. Read by ``test_registered_tools_accept_socket_name`` to
#: enforce the agent-facing contract advertised in
#: :data:`libtmux_mcp.server._BASE_INSTRUCTIONS`. When you add a new
#: discovery-style tool, append it here AND update the prose in
#: ``_BASE_INSTRUCTIONS`` so the two stay in lockstep.
SOCKET_NAME_EXEMPT: frozenset[str] = frozenset({"list_servers"})


@handle_tool_errors
def list_servers(
    extra_socket_paths: list[str] | None = None,
) -> list[ServerInfo]:
    """Discover live tmux servers under the current user's ``$TMUX_TMPDIR``.

    Scans ``${TMUX_TMPDIR:-/tmp}/tmux-<uid>/`` for socket files — the
    canonical location where tmux creates per-server sockets (see
    tmux.c's ``expand_paths`` + ``TMUX_SOCK`` template). Only sockets
    with a live listener are reported; stale inodes (a common case on
    long-running systems where ``$TMUX_TMPDIR`` can carry thousands of
    orphans) are silently filtered.

    **Scope caveat**: custom ``tmux -S /some/path/...`` servers that
    live OUTSIDE ``$TMUX_TMPDIR`` are not returned by the scan alone —
    there is no canonical registry for arbitrary socket paths. Supply
    known paths via ``extra_socket_paths`` to include them in the
    result, or pass the path to other tools via their ``socket_name``
    / ``socket_path`` parameters once known.

    Parameters
    ----------
    extra_socket_paths : list of str, optional
        Additional filesystem paths to probe alongside the
        ``$TMUX_TMPDIR`` scan. Each path is checked for liveness (UNIX
        ``connect()``) and queried for server metadata. Paths that do
        not exist, are not sockets, or have no listener are silently
        skipped.

    Returns
    -------
    list[ServerInfo]
        One entry per live tmux server found. Canonical-directory
        results come first, followed by successful ``extra_socket_paths``
        probes in the supplied order. Empty when nothing lives under
        ``$TMUX_TMPDIR`` and no extras are supplied or reachable.
    """
    tmux_tmpdir = os.environ.get("TMUX_TMPDIR", "/tmp")
    uid_dir = pathlib.Path(tmux_tmpdir) / f"tmux-{os.geteuid()}"
    results: list[ServerInfo] = []
    if uid_dir.is_dir():
        for entry in sorted(uid_dir.iterdir()):
            try:
                if not entry.is_socket():
                    continue
            except OSError:
                continue
            # Cheap liveness probe before the more expensive
            # ``get_server_info`` call. Stale sockets are the common case.
            if not _is_tmux_socket_live(entry):
                continue
            try:
                info = get_server_info(socket_name=entry.name)
            except ToolError:
                continue
            results.append(info)
    for raw_path in extra_socket_paths or []:
        extra = _probe_server_by_path(pathlib.Path(raw_path))
        if extra is not None:
            results.append(extra)
    return results


def register(mcp: FastMCP) -> None:
    """Register server-level tools with the MCP instance."""
    mcp.tool(title="List Sessions", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        list_sessions
    )
    mcp.tool(title="List Servers", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        list_servers
    )
    mcp.tool(
        title="Create Session", annotations=ANNOTATIONS_CREATE, tags={TAG_MUTATING}
    )(create_session)
    mcp.tool(
        title="Kill Server", annotations=ANNOTATIONS_DESTRUCTIVE, tags={TAG_DESTRUCTIVE}
    )(kill_server)
    mcp.tool(title="Get Server Info", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        get_server_info
    )
