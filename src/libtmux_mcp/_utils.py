"""Shared utilities for libtmux MCP server.

Provides server caching, object resolution, serialization, and error handling
for all MCP tool functions.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import logging
import os
import pathlib
import threading
import typing as t

from fastmcp.exceptions import ToolError
from libtmux import exc
from libtmux._internal.query_list import LOOKUP_NAME_MAP
from libtmux.server import Server

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.session import Session
    from libtmux.window import Window

    from libtmux_mcp.models import PaneInfo, SessionInfo, WindowInfo

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CallerIdentity:
    """Identity of the tmux pane hosting this MCP server process.

    Parsed from the ``TMUX`` and ``TMUX_PANE`` environment variables that
    tmux injects into every child of a pane. ``TMUX`` has the format
    ``socket_path,server_pid,session_id`` (see tmux ``environ.c:281``).

    Used to scope self-protection checks to the caller's own tmux server —
    a pane ID like ``%1`` is only unique within a single server, so
    comparisons must also verify the socket path matches.
    """

    socket_path: str | None
    server_pid: int | None
    session_id: str | None
    pane_id: str | None


def _get_caller_identity() -> CallerIdentity | None:
    """Return the caller's tmux identity, or None if not inside tmux.

    Reads ``TMUX`` for socket_path/server_pid/session_id and ``TMUX_PANE``
    for the pane id. Tolerant of missing/malformed ``TMUX`` values —
    callers should check individual fields rather than relying on all
    being populated.
    """
    pane_id = os.environ.get("TMUX_PANE")
    tmux_env = os.environ.get("TMUX")

    if not tmux_env and not pane_id:
        return None

    socket_path: str | None = None
    server_pid: int | None = None
    session_id: str | None = None

    if tmux_env:
        parts = tmux_env.split(",", 2)
        if parts:
            socket_path = parts[0] or None
        if len(parts) >= 2 and parts[1]:
            try:
                server_pid = int(parts[1])
            except ValueError:
                server_pid = None
        if len(parts) >= 3 and parts[2]:
            session_id = parts[2]

    return CallerIdentity(
        socket_path=socket_path,
        server_pid=server_pid,
        session_id=session_id,
        pane_id=pane_id,
    )


def _compute_is_caller(pane: Pane) -> bool | None:
    """Decide whether ``pane`` is the MCP caller's own tmux pane.

    The returned value is used as the ``is_caller`` annotation on
    :class:`~libtmux_mcp.models.PaneInfo`,
    :class:`~libtmux_mcp.models.PaneSnapshot`, and
    :class:`~libtmux_mcp.models.PaneContentMatch`.

    Tri-state semantics match the original bare-equality check:

    * ``None`` — process is not inside tmux at all (neither ``TMUX`` nor
      ``TMUX_PANE`` are set). No caller exists, so the annotation
      carries no signal.
    * ``True`` — the caller's ``TMUX_PANE`` matches ``pane.pane_id``
      *and* :func:`_caller_is_strictly_on_server` confirms the
      caller's socket realpath equals the target's.
    * ``False`` — the pane ids differ, or they match but the socket
      does not (or cannot be proven to). A bare pane-id equality
      check would have returned ``True`` here, which is the
      cross-socket false-positive fixed by
      tmux-python/libtmux-mcp#19.

    Uses :func:`_caller_is_strictly_on_server` rather than
    :func:`_caller_is_on_server`: the kill-guard comparator is
    conservative-True-when-uncertain (right for blocking destructive
    actions, wrong for an informational annotation that should
    demand a positive match). The strict variant declines the
    basename fallback, the unresolvable-target branch, and the
    socket-path-unset branch so ambiguous cases resolve to ``False``.
    """
    caller = _get_caller_identity()
    if caller is None or caller.pane_id is None:
        return None
    return caller.pane_id == pane.pane_id and _caller_is_strictly_on_server(
        pane.server, caller
    )


def _effective_socket_path(server: Server) -> str | None:
    """Return the filesystem socket path a Server will actually use.

    libtmux leaves ``Server.socket_path`` as ``None`` when only
    ``socket_name`` (or neither) was supplied, but tmux still resolves to
    a real path under ``${TMUX_TMPDIR:-/tmp}/tmux-<uid>/<name>``. This
    helper reproduces that resolution so :func:`_caller_is_on_server` can
    compare against the caller's ``TMUX`` socket path.

    Resolution order:

    1. ``Server.socket_path`` if libtmux already has it.
    2. ``tmux display-message -p '#{socket_path}'`` against the target
       server — authoritative because tmux itself reports the path it
       is actually using, regardless of our process environment.
       Necessary on macOS where ``$TMUX_TMPDIR`` under launchd diverges
       from the interactive shell (see ``docs/topics/safety.md`` for
       the self-kill guard gap this closes).
    3. Fallback: reconstruct from ``$TMUX_TMPDIR`` + euid + socket name.
       This path is reached only when the target server is unreachable
       (e.g. not running), in which case no self-kill is possible and
       the conservative caller check still blocks via
       ``_caller_is_on_server``'s None-socket branch.
    """
    if server.socket_path:
        return str(server.socket_path)
    # Preferred: ask tmux directly. ``display-message -p`` prints the
    # value to stdout and exits, so this is cheap. Wrapped defensively
    # because the server may be down, the format may be unsupported on
    # ancient tmux, or permissions may deny the call.
    try:
        resolved = server.cmd(
            "display-message",
            "-p",
            "#{socket_path}",
        ).stdout
    except (exc.LibTmuxException, OSError):
        resolved = None
    if resolved:
        first = resolved[0].strip()
        if first:
            return first
    tmux_tmpdir = os.environ.get("TMUX_TMPDIR", "/tmp")
    socket_name = server.socket_name or "default"
    return str(pathlib.Path(tmux_tmpdir) / f"tmux-{os.geteuid()}" / socket_name)


def _caller_is_on_server(server: Server, caller: CallerIdentity | None) -> bool:
    """Return True if ``caller`` looks like it is on the same tmux server.

    Compares socket paths via :func:`os.path.realpath` so symlinked temp
    dirs still match, then falls back to basename comparison when
    realpath disagrees — the authoritative caller-side ``$TMUX`` name
    and the target's declared ``socket_name`` are both unaffected by
    ``$TMUX_TMPDIR`` divergence (the macOS launchd case), so a
    last-chance name match still blocks a self-kill when the path
    comparison was fooled by env mismatch.

    Decision table:

    * ``caller is None`` → ``False``. The process isn't inside tmux at
      all, so there is no caller-side pane to protect and no self-kill
      is possible.
    * caller has a pane id but no socket path (e.g. ``TMUX_PANE`` set
      without ``TMUX``) → ``True``. We can't rule out that the caller
      is on the target server, so err on the side of blocking a
      destructive action.
    * target server has no resolvable socket path → ``True``. Same
      conservative reasoning.
    * realpath of caller's socket path matches target's effective path
      → ``True`` (primary positive signal).
    * basename of caller's socket path equals target's
      ``socket_name`` (or ``"default"``) → ``True``. Conservative
      last-chance block for env-mismatch scenarios where reconstruction
      produced a wrong path but the name was authoritative on both
      sides. Trades off one exotic false positive (two daemons with
      identical socket_name under different tmpdirs) for a real safety
      property.
    * Otherwise → ``False``.

    When a conservative block is a false positive, the caller's error
    message directs the user to run tmux manually.
    """
    if caller is None:
        return False
    if not caller.socket_path:
        return caller.pane_id is not None
    target = _effective_socket_path(server)
    if not target:
        return True
    try:
        if os.path.realpath(caller.socket_path) == os.path.realpath(target):
            return True
    except OSError:
        if caller.socket_path == target:
            return True
    # Final conservative check: names match even though paths didn't.
    # Survives ``$TMUX_TMPDIR`` divergence between the MCP process and
    # the caller's shell (macOS launchd).
    caller_basename = pathlib.PurePath(caller.socket_path).name
    target_name = server.socket_name or "default"
    return caller_basename == target_name


def _caller_is_strictly_on_server(
    server: Server, caller: CallerIdentity | None
) -> bool:
    """Return True only on a confirmed socket-path match.

    Counterpart to :func:`_caller_is_on_server` for the informational
    :attr:`~libtmux_mcp.models.PaneInfo.is_caller` annotation. The
    destructive-action guard is biased toward True-when-uncertain so a
    macOS ``$TMUX_TMPDIR`` divergence cannot fool it into permitting
    self-kill; the annotation cannot absorb that bias — ambiguous cases
    are exactly the cross-socket false positives documented by
    tmux-python/libtmux-mcp#19. This function therefore declines every
    branch other than a confirmed ``realpath`` match.

    Decision table:

    * ``caller is None`` → ``False``. No caller identity.
    * ``caller.socket_path`` unset (``TMUX_PANE`` set without ``TMUX``)
      → ``False``. We cannot verify the caller is on this server.
    * target server's effective socket path unresolvable → ``False``.
    * ``realpath`` of caller's socket path equals target's effective
      path → ``True``. Primary and only positive signal.
    * Fallback on ``OSError`` from ``realpath``: exact string match
      → ``True``. Still a positive signal, just without the resolve
      step.
    * Otherwise → ``False`` (including the basename-only match that
      :func:`_caller_is_on_server` permits as a conservative block).
    """
    if caller is None or not caller.socket_path:
        return False
    target = _effective_socket_path(server)
    if not target:
        return False
    try:
        return os.path.realpath(caller.socket_path) == os.path.realpath(target)
    except OSError:
        return caller.socket_path == target


# ---------------------------------------------------------------------------
# Safety tier tags
# ---------------------------------------------------------------------------

TAG_READONLY = "readonly"
TAG_MUTATING = "mutating"
TAG_DESTRUCTIVE = "destructive"

VALID_SAFETY_LEVELS = frozenset({TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE})

# ---------------------------------------------------------------------------
# Reusable annotation presets for tool registration
# ---------------------------------------------------------------------------

ANNOTATIONS_RO: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
ANNOTATIONS_MUTATING: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
ANNOTATIONS_CREATE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
#: Annotations for tools that move user-supplied payloads into a shell
#: context. Five consumers today:
#:
#: * ``send_keys``, ``paste_text``, ``pipe_pane`` — the canonical
#:   shell-driving tools; caller's keys/text/stream reaches the shell
#:   prompt or pipes into an external command respectively.
#: * ``load_buffer``, ``paste_buffer`` — ``load_buffer`` stages content
#:   into a tmux paste buffer; ``paste_buffer`` pushes that content
#:   into a target pane where the shell receives it as input. The two
#:   are split into a stage/fire pair so callers can validate before
#:   paste, but both participate in the same open-world transfer.
#:
#: Distinguished from :data:`ANNOTATIONS_CREATE` by ``openWorldHint=True``:
#: the effects of these tools extend into whatever command or content
#: the caller supplies, which is the canonical open-world MCP
#: interaction.
ANNOTATIONS_SHELL: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
ANNOTATIONS_DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}


def _tmux_argv(server: Server, *tmux_args: str) -> list[str]:
    """Build a full tmux argv list honouring ``socket_name`` and ``socket_path``.

    Internal helper shared by every module that has to invoke the tmux
    binary directly via :func:`subprocess.run` (the buffer, wait-for,
    and paste_text tools). libtmux's own :meth:`Server.cmd` wraps the
    same logic but does not expose a timeout, so tools that need
    bounded blocking have to shell out themselves — and when they do
    they must honour the caller's socket.

    Parameters
    ----------
    server : libtmux.server.Server
        The resolved server whose socket to target.
    *tmux_args : str
        tmux subcommand and its flags, e.g. ``"load-buffer", "-b", name``.

    Returns
    -------
    list[str]
        Complete argv ready for :func:`subprocess.run`.

    Examples
    --------
    >>> class _S:
    ...     tmux_bin = "tmux"
    ...     socket_name = "s"
    ...     socket_path = None
    >>> _tmux_argv(t.cast("Server", _S()), "list-sessions")
    ['tmux', '-L', 's', 'list-sessions']

    >>> class _P:
    ...     tmux_bin = "tmux"
    ...     socket_name = None
    ...     socket_path = "/tmp/tmux-1000/default"
    >>> _tmux_argv(t.cast("Server", _P()), "ls")
    ['tmux', '-S', '/tmp/tmux-1000/default', 'ls']
    """
    tmux_bin: str = getattr(server, "tmux_bin", None) or "tmux"
    argv: list[str] = [tmux_bin]
    if server.socket_name:
        argv.extend(["-L", server.socket_name])
    if server.socket_path:
        argv.extend(["-S", str(server.socket_path)])
    argv.extend(tmux_args)
    return argv


_server_cache: dict[tuple[str | None, str | None, str | None], Server] = {}
_server_cache_lock = threading.Lock()


def _get_server(
    socket_name: str | None = None,
    socket_path: str | None = None,
) -> Server:
    """Get or create a cached Server instance.

    Parameters
    ----------
    socket_name : str, optional
        tmux socket name (-L). Falls back to LIBTMUX_SOCKET env var.
    socket_path : str, optional
        tmux socket path (-S). Falls back to LIBTMUX_SOCKET_PATH env var.

    Returns
    -------
    Server
        A cached libtmux Server instance.
    """
    if socket_name is None:
        socket_name = os.environ.get("LIBTMUX_SOCKET")
    if socket_path is None:
        socket_path = os.environ.get("LIBTMUX_SOCKET_PATH")

    tmux_bin = os.environ.get("LIBTMUX_TMUX_BIN")

    cache_key = (socket_name, socket_path, tmux_bin)
    with _server_cache_lock:
        if cache_key in _server_cache:
            cached = _server_cache[cache_key]
            if not cached.is_alive():
                del _server_cache[cache_key]

        if cache_key not in _server_cache:
            kwargs: dict[str, t.Any] = {}
            if socket_name is not None:
                kwargs["socket_name"] = socket_name
            if socket_path is not None:
                kwargs["socket_path"] = socket_path
            if tmux_bin is not None:
                kwargs["tmux_bin"] = tmux_bin
            _server_cache[cache_key] = Server(**kwargs)

        return _server_cache[cache_key]


def _invalidate_server(
    socket_name: str | None = None,
    socket_path: str | None = None,
) -> None:
    """Evict a server from the cache.

    Parameters
    ----------
    socket_name : str, optional
        tmux socket name used in the cache key.
    socket_path : str, optional
        tmux socket path used in the cache key.
    """
    if socket_name is None:
        socket_name = os.environ.get("LIBTMUX_SOCKET")
    if socket_path is None:
        socket_path = os.environ.get("LIBTMUX_SOCKET_PATH")

    with _server_cache_lock:
        keys_to_remove = [
            key
            for key in _server_cache
            if key[0] == socket_name and key[1] == socket_path
        ]
        for key in keys_to_remove:
            del _server_cache[key]


def _resolve_session(
    server: Server,
    session_name: str | None = None,
    session_id: str | None = None,
) -> Session:
    """Resolve a session by name or ID.

    Parameters
    ----------
    server : Server
        The tmux server.
    session_name : str, optional
        Session name to look up.
    session_id : str, optional
        Session ID (e.g. '$1') to look up.

    Returns
    -------
    Session

    Raises
    ------
    exc.TmuxObjectDoesNotExist
        If no matching session is found.
    """
    if session_id is not None:
        session = server.sessions.get(session_id=session_id, default=None)
        if session is None:
            raise exc.TmuxObjectDoesNotExist(
                obj_key="session_id",
                obj_id=session_id,
                list_cmd="list-sessions",
            )
        return session

    if session_name is not None:
        session = server.sessions.get(session_name=session_name, default=None)
        if session is None:
            raise exc.TmuxObjectDoesNotExist(
                obj_key="session_name",
                obj_id=session_name,
                list_cmd="list-sessions",
            )
        return session

    sessions = server.sessions
    if not sessions:
        raise exc.TmuxObjectDoesNotExist(
            obj_key="session",
            obj_id="(any)",
            list_cmd="list-sessions",
        )
    return sessions[0]


def _resolve_window(
    server: Server,
    session: Session | None = None,
    window_id: str | None = None,
    window_index: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
) -> Window:
    """Resolve a window by ID, index, or default.

    Parameters
    ----------
    server : Server
        The tmux server.
    session : Session, optional
        Session to search within.
    window_id : str, optional
        Window ID (e.g. '@1').
    window_index : str, optional
        Window index within the session.
    session_name : str, optional
        Session name for resolution.
    session_id : str, optional
        Session ID for resolution.

    Returns
    -------
    Window

    Raises
    ------
    exc.TmuxObjectDoesNotExist
        If no matching window is found.
    """
    if window_id is not None:
        window = server.windows.get(window_id=window_id, default=None)
        if window is None:
            raise exc.TmuxObjectDoesNotExist(
                obj_key="window_id",
                obj_id=window_id,
                list_cmd="list-windows",
            )
        return window

    if session is None:
        session = _resolve_session(
            server,
            session_name=session_name,
            session_id=session_id,
        )

    if window_index is not None:
        window = session.windows.get(window_index=window_index, default=None)
        if window is None:
            raise exc.TmuxObjectDoesNotExist(
                obj_key="window_index",
                obj_id=window_index,
                list_cmd="list-windows",
            )
        return window

    windows = session.windows
    if not windows:
        raise exc.NoWindowsExist()
    return windows[0]


def _resolve_pane(
    server: Server,
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    window_index: str | None = None,
    pane_index: str | None = None,
) -> Pane:
    """Resolve a pane by ID or hierarchical targeting.

    Parameters
    ----------
    server : Server
        The tmux server.
    pane_id : str, optional
        Pane ID (e.g. '%1'). Globally unique within a server.
    session_name : str, optional
        Session name for hierarchical resolution.
    session_id : str, optional
        Session ID for hierarchical resolution.
    window_id : str, optional
        Window ID for hierarchical resolution.
    window_index : str, optional
        Window index for hierarchical resolution.
    pane_index : str, optional
        Pane index within the window.

    Returns
    -------
    Pane

    Raises
    ------
    exc.TmuxObjectDoesNotExist
        If no matching pane is found.
    """
    if pane_id is not None:
        pane = server.panes.get(pane_id=pane_id, default=None)
        if pane is None:
            raise exc.PaneNotFound(pane_id=pane_id)
        return pane

    window = _resolve_window(
        server,
        window_id=window_id,
        window_index=window_index,
        session_name=session_name,
        session_id=session_id,
    )

    if pane_index is not None:
        pane = window.panes.get(pane_index=pane_index, default=None)
        if pane is None:
            raise exc.PaneNotFound(pane_id=f"index:{pane_index}")
        return pane

    panes = window.panes
    if not panes:
        raise exc.PaneNotFound()
    return panes[0]


M = t.TypeVar("M")


def _coerce_dict_arg(
    name: str,
    value: dict[str, t.Any] | str | None,
) -> dict[str, t.Any] | None:
    """Coerce a tool parameter to a dict, accepting JSON-string form.

    Workaround: Cursor's composer-1/composer-1.5 models and some other
    MCP clients serialize dict params as JSON strings instead of
    objects. Claude and GPT models through Cursor work fine; the bug
    is model-specific. This helper is the canonical place to absorb
    the string form so each tool can stay dict-typed on the Python
    side. Callers pass ``name`` so the error messages identify the
    offending parameter.

    See:
        https://forum.cursor.com/t/145807
        https://github.com/anthropics/claude-code/issues/5504

    Parameters
    ----------
    name : str
        Parameter name, used in error messages.
    value : dict, str, or None
        Either an already-decoded dict, a JSON string of a dict, or
        ``None``.

    Returns
    -------
    dict or None
        The decoded dict, or ``None`` if the input was ``None`` or an
        empty string.

    Raises
    ------
    ToolError
        If ``value`` is a string that is not valid JSON, or decodes to
        a JSON value that is not an object.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, ValueError) as e:
            msg = f"Invalid {name} JSON: {e}"
            raise ToolError(msg) from e
        if not isinstance(decoded, dict):
            msg = f"{name} must be a JSON object, got {type(decoded).__name__}"
            raise ToolError(msg) from None
        return decoded
    return value


def _apply_filters(
    items: t.Any,
    filters: dict[str, str] | str | None,
    serializer: t.Callable[..., M],
) -> list[M]:
    """Apply QueryList filters and serialize results.

    Parameters
    ----------
    items : QueryList
        The QueryList of tmux objects to filter.
    filters : dict or str, optional
        Django-style filters as a dict (e.g. ``{"session_name__contains": "dev"}``)
        or as a JSON string. Some MCP clients require the string form.
        If None or empty, all items are returned.
    serializer : callable
        Serializer function to convert each item to a model.

    Returns
    -------
    list
        Serialized list of matching items.

    Raises
    ------
    ToolError
        If a filter key uses an invalid lookup operator.
    """
    coerced = _coerce_dict_arg("filters", filters)
    if not coerced:
        return [serializer(item) for item in items]
    filters = coerced

    valid_ops = sorted(LOOKUP_NAME_MAP.keys())
    for key in filters:
        if "__" in key:
            _field, op = key.rsplit("__", 1)
            if op not in LOOKUP_NAME_MAP:
                msg = (
                    f"Invalid filter operator '{op}' in '{key}'. "
                    f"Valid operators: {', '.join(valid_ops)}"
                )
                raise ToolError(msg)

    filtered = items.filter(**filters)
    return [serializer(item) for item in filtered]


def _serialize_session(session: Session) -> SessionInfo:
    """Serialize a Session to a Pydantic model.

    Parameters
    ----------
    session : Session
        The session to serialize.

    Returns
    -------
    SessionInfo
        Session data including id, name, window count.
    """
    from libtmux_mcp.models import SessionInfo

    assert session.session_id is not None
    # Defensive ``getattr``: ``Session.active_pane`` exists on every
    # supported libtmux version, but older builds may raise instead of
    # returning ``None`` for sessions mid-teardown. Treating a missing
    # attribute or missing pane id as ``None`` lets ``list_sessions``
    # tolerate transient state without breaking serialization.
    active_pane = getattr(session, "active_pane", None)
    active_pane_id = active_pane.pane_id if active_pane is not None else None

    return SessionInfo(
        session_id=session.session_id,
        session_name=session.session_name,
        window_count=len(session.windows),
        session_attached=getattr(session, "session_attached", None),
        session_created=getattr(session, "session_created", None),
        active_pane_id=active_pane_id,
    )


def _serialize_window(window: Window) -> WindowInfo:
    """Serialize a Window to a Pydantic model.

    Parameters
    ----------
    window : Window
        The window to serialize.

    Returns
    -------
    WindowInfo
        Window data including id, name, index, pane count, layout.
    """
    from libtmux_mcp.models import WindowInfo

    assert window.window_id is not None
    return WindowInfo(
        window_id=window.window_id,
        window_name=window.window_name,
        window_index=window.window_index,
        session_id=window.session_id,
        session_name=getattr(window, "session_name", None),
        pane_count=len(window.panes),
        window_layout=getattr(window, "window_layout", None),
        window_active=getattr(window, "window_active", None),
        window_width=getattr(window, "window_width", None),
        window_height=getattr(window, "window_height", None),
    )


def _serialize_pane(pane: Pane) -> PaneInfo:
    """Serialize a Pane to a Pydantic model.

    Parameters
    ----------
    pane : Pane
        The pane to serialize.

    Returns
    -------
    PaneInfo
        Pane data including id, dimensions, current command, title.
    """
    from libtmux_mcp.models import PaneInfo

    assert pane.pane_id is not None
    return PaneInfo(
        pane_id=pane.pane_id,
        pane_index=getattr(pane, "pane_index", None),
        pane_width=getattr(pane, "pane_width", None),
        pane_height=getattr(pane, "pane_height", None),
        pane_current_command=getattr(pane, "pane_current_command", None),
        pane_current_path=getattr(pane, "pane_current_path", None),
        pane_pid=getattr(pane, "pane_pid", None),
        pane_title=getattr(pane, "pane_title", None),
        pane_active=getattr(pane, "pane_active", None),
        window_id=pane.window_id,
        session_id=pane.session_id,
        is_caller=_compute_is_caller(pane),
    )


P = t.ParamSpec("P")
R = t.TypeVar("R")


def _map_exception_to_tool_error(fn_name: str, e: BaseException) -> ToolError:
    """Translate a libtmux / unexpected exception into a ``ToolError``.

    Shared between the sync and async ``handle_tool_errors*`` decorators
    so the two paths stay byte-for-byte identical in what agents see.
    """
    if isinstance(e, exc.TmuxCommandNotFound):
        msg = "tmux binary not found. Ensure tmux is installed and in PATH."
        return ToolError(msg)
    if isinstance(e, exc.TmuxSessionExists):
        return ToolError(str(e))
    if isinstance(e, exc.BadSessionName):
        return ToolError(str(e))
    if isinstance(e, exc.TmuxObjectDoesNotExist):
        return ToolError(f"Object not found: {e}")
    if isinstance(e, exc.PaneNotFound):
        return ToolError(f"Pane not found: {e}")
    if isinstance(e, exc.LibTmuxException):
        return ToolError(f"tmux error: {e}")
    logger.exception("unexpected error in MCP tool %s", fn_name)
    return ToolError(f"Unexpected error: {type(e).__name__}: {e}")


def handle_tool_errors(
    fn: t.Callable[P, R],
) -> t.Callable[P, R]:
    """Decorate synchronous MCP tool functions with standardized error handling.

    Catches libtmux exceptions and re-raises as ``ToolError`` so that
    MCP responses have ``isError=True`` with a descriptive message.
    Use :func:`handle_tool_errors_async` for ``async def`` tools — this
    wrapper only supports plain sync callables.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise _map_exception_to_tool_error(fn.__name__, e) from e

    return wrapper


def handle_tool_errors_async(
    fn: t.Callable[P, t.Coroutine[t.Any, t.Any, R]],
) -> t.Callable[P, t.Coroutine[t.Any, t.Any, R]]:
    """Decorate asynchronous MCP tool functions with standardized error handling.

    Async counterpart to :func:`handle_tool_errors`. Required for tools
    that accept a :class:`fastmcp.Context` parameter because Context's
    ``report_progress``/``elicit``/``read_resource`` methods are
    coroutines that only run inside ``async def`` tools.

    Maps the same libtmux exception set to the same ``ToolError``
    messages as the sync decorator by delegating to a shared helper.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            raise _map_exception_to_tool_error(fn.__name__, e) from e

    return wrapper
