"""Shared utilities for libtmux MCP server.

Provides server caching, object resolution, serialization, and error handling
for all MCP tool functions.
"""

from __future__ import annotations

import dataclasses
import difflib
import functools
import json
import logging
import os
import pathlib
import threading
import typing as t

from fastmcp.exceptions import ToolError
from libtmux import exc
from libtmux._internal.query_list import LOOKUP_NAME_MAP, QueryList
from libtmux.server import Server

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.session import Session
    from libtmux.window import Window
    from pydantic import BaseModel

    from libtmux_mcp.models import PaneInfo, SessionInfo, WindowInfo

logger = logging.getLogger(__name__)


class ExpectedToolError(ToolError):
    """``ToolError`` for expected, agent-correctable failures.

    Defaults the error's ``log_level`` to ``WARNING`` (honored by
    fastmcp >= 3.3 when logging tool/resource failures) so routine
    validation errors, missing objects, and tier denials do not surface
    as ERROR records. Unexpected failures keep stock :class:`ToolError`
    and its ERROR default — those are the ones operators must see.

    Parameters
    ----------
    *args : object
        Positional arguments forwarded to :class:`ToolError`
        (typically the error message).
    log_level : int
        Level fastmcp's server layer logs this failure at. Defaults
        to ``logging.WARNING``.
    suggestion : str, optional
        Agent-facing recovery hint.
        :class:`~libtmux_mcp.middleware.ToolErrorResultMiddleware`
        appends it to the error result's text and mirrors it into the
        result's ``meta``.

    Examples
    --------
    >>> import logging
    >>> ExpectedToolError("Pane not found: %5").log_level == logging.WARNING
    True

    An explicit level still wins:

    >>> err = ExpectedToolError("noisy", log_level=logging.INFO)
    >>> err.log_level == logging.INFO
    True

    Catch sites that handle ``ToolError`` keep working — this is a
    plain subclass:

    >>> isinstance(ExpectedToolError("x"), ToolError)
    True

    An optional ``suggestion`` carries an agent-facing recovery hint;
    :class:`libtmux_mcp.middleware.ToolErrorResultMiddleware` surfaces
    it in the error result's text and ``meta``:

    >>> err = ExpectedToolError("Pane not found: %5",
    ...     suggestion="Call list_panes to discover valid pane ids.")
    >>> err.suggestion
    'Call list_panes to discover valid pane ids.'
    >>> ExpectedToolError("no hint").suggestion is None
    True
    """

    def __init__(
        self,
        *args: object,
        log_level: int = logging.WARNING,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(*args, log_level=log_level)
        self.suggestion = suggestion


@dataclasses.dataclass(frozen=True)
class CallerIdentity:
    """Identity of the tmux pane hosting this MCP server process.

    Parsed from the ``TMUX`` and ``TMUX_PANE`` environment variables that
    tmux injects into every child of a pane. ``TMUX`` has the format
    ``socket_path,server_pid,session_id`` (see tmux ``environ.c:281``).

    Used to scope self-protection checks to the caller's own tmux server —
    a pane ID like ``%1`` is only unique within a single server, so
    comparisons must also verify the socket path matches.

    Attributes
    ----------
    socket_path : str | None
        Filesystem path of the tmux socket the caller is attached to, from
        the first ``TMUX`` field. ``None`` when ``TMUX`` is unset or its
        first field is empty — the socket comparisons in
        :func:`_caller_is_on_server` and
        :func:`_caller_is_strictly_on_server` then cannot prove which
        server the caller belongs to.
    server_pid : int | None
        PID of the tmux server process, from the second ``TMUX`` field.
        ``None`` when that field is absent or not an integer.
    session_id : str | None
        Session the caller's pane belongs to (e.g. ``$7``), from the third
        ``TMUX`` field. ``None`` when that field is absent or empty.
    pane_id : str | None
        Pane the MCP server process runs in (e.g. ``%3``), read from
        ``TMUX_PANE``. ``None`` when the variable is unset, meaning no
        pane can be identified as the caller's own.
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

    libtmux leaves :attr:`libtmux.Server.socket_path` as ``None`` when only
    ``socket_name`` (or neither) was supplied, but tmux still resolves to
    a real path under ``${TMUX_TMPDIR:-/tmp}/tmux-<uid>/<name>``. This
    helper reproduces that resolution so :func:`_caller_is_on_server` can
    compare against the caller's ``TMUX`` socket path.

    Resolution order:

    1. :attr:`libtmux.Server.socket_path` if libtmux already has it.
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

#: Non-tier marker tag for tools that enforce their own wall-clock
#: ceiling internally and whose cost is therefore *duration*, not
#: side effects.
#:
#: A tagged tool must never be re-driven by machinery that assumes a
#: call is cheap:
#:
#: * :class:`~libtmux_mcp.middleware.ReadonlyRetryMiddleware` skips it,
#:   because the deadline is computed inside the tool body — a retry
#:   restarts the clock and doubles the ceiling.
#: * The ``call_*_tools_batch`` wrappers reject it per-operation,
#:   because the batch loop is serial with no aggregate deadline and
#:   ``MAX_BATCH_OPERATIONS`` is 1000.
#:
#: A TAG rather than a tool-name list on purpose: a name string is
#: exactly what ``add_tool_transformation`` can rename out from under
#: the exclusion. Tier resolution
#: (:meth:`~libtmux_mcp.middleware.SafetyMiddleware._is_allowed`,
#: ``batch_tools._tool_tier``) inspects only the three tier tags, so
#: carrying this extra tag is inert everywhere else.
TAG_SELF_BOUNDED = "self-bounded"

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
#: context. Six consumers today:
#:
#: * ``send_keys``, ``run_command``, ``paste_text``, ``pipe_pane`` — the
#:   canonical shell-driving tools; caller's keys/command/text/stream
#:   reaches the shell prompt or pipes into an external command
#:   respectively.
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

#: Per-tool MCP ``meta`` payload that hints clients to keep this tool
#: always visible (not deferred). FastMCP passes ``meta`` opaquely
#: (verified vs ``~/study/python/fastmcp/src`` — no special handling);
#: honoring is delegated to Claude Code, where ``alwaysLoad`` is
#: documented at https://code.claude.com/docs/en/mcp (v2.1.121+).
#:
#: Best-effort by design — safe no-op for clients that don't index the
#: ``anthropic/*`` namespace. Apply only to read-tier discovery anchors
#: (``list_panes``, ``list_windows``, ``snapshot_pane``); each
#: always-loaded tool consumes a fixed schema budget in clients that
#: honour the hint, so widening the set has a real cost.
DISCOVERY_META: dict[str, t.Any] = {
    "anthropic/alwaysLoad": True,
}
#: Annotations for tools that stay in the ``mutating`` tier (so they remain
#: visible to default-profile agents) but whose default behaviour can
#: terminate processes or otherwise lose state.
#:
#: Canonical users include ``respawn_pane`` and ``clear_pane``:
#: tier=mutating because shell recovery and scrollback cleanup are part
#: of normal agent workflows, while the hints still disclose process
#: termination or state loss.
#:
#: Distinct from :data:`ANNOTATIONS_DESTRUCTIVE` (same hint values) because
#: the tier tag differs: ``ANNOTATIONS_DESTRUCTIVE`` is paired with
#: ``TAG_DESTRUCTIVE`` everywhere it is used; this preset is paired with
#: ``TAG_MUTATING``. The distinct name documents intent at the call site.
ANNOTATIONS_MUTATING_DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}


def _tmux_argv(server: Server, *tmux_args: str) -> list[str]:
    """Build a full tmux argv list honouring ``socket_name`` and ``socket_path``.

    Internal helper shared by every module that has to invoke the tmux
    binary directly via :func:`subprocess.run` (the buffer, wait-for,
    and paste_text tools). libtmux's own :meth:`libtmux.Server.cmd` wraps the
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
                list_extra_args=(),
            )
        return session

    if session_name is not None:
        session = server.sessions.get(session_name=session_name, default=None)
        if session is None:
            raise exc.TmuxObjectDoesNotExist(
                obj_key="session_name",
                obj_id=session_name,
                list_cmd="list-sessions",
                list_extra_args=(),
            )
        return session

    sessions = server.sessions
    if not sessions:
        raise exc.TmuxObjectDoesNotExist(
            obj_key="session",
            obj_id="(any)",
            list_cmd="list-sessions",
            list_extra_args=(),
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
                list_extra_args=(),
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
                list_extra_args=(),
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
    ExpectedToolError
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
            raise ExpectedToolError(msg) from e
        if not isinstance(decoded, dict):
            msg = f"{name} must be a JSON object, got {type(decoded).__name__}"
            raise ExpectedToolError(msg) from None
        return decoded
    return value


@functools.cache
def _filterable_fields(obj_type: type) -> frozenset[str]:
    """Attribute names a filter key may begin with.

    ``QueryList`` resolves a key by ``getattr`` traversal and treats a
    miss as "no match", so an unknown field silently filters every row
    out and an empty result is indistinguishable from a typo.

    Deliberately permissive: it rejects names the type cannot have and
    accepts everything else, because ``__`` traversal into a nested
    object is legitimate and only the first segment is checkable here.
    """
    names = {name for name in dir(obj_type) if not name.startswith("_")}
    if dataclasses.is_dataclass(obj_type):
        names |= {field.name for field in dataclasses.fields(obj_type)}
    return frozenset(names)


_MODEL_FIELD_ALIASES: dict[str, str] = {
    "window_count": "session_windows",
    "pane_count": "window_panes",
    "active_pane_id": "active_pane__pane_id",
}
"""Output fields tmux exposes under a different attribute name."""


def _admits_bool(annotation: t.Any) -> bool:
    """Whether a model field's annotation can hold a bool."""
    return annotation is bool or bool in t.get_args(annotation)


_BOOL_TRUE = frozenset({"true", "1", "yes"})
_BOOL_FALSE = frozenset({"false", "0", "no"})

#: Operators that mean anything against a bool. The rest are string or
#: collection tests; libtmux's lookups fall through to ``return False``
#: for a bool, so allowing them would answer every query with an empty
#: list -- including contradictory pairs like ``__in``/``__nin``.
_BOOL_OPERATORS = frozenset({"exact", "eq"})


def _coerce_model_value(key: str, value: t.Any, annotation: t.Any) -> t.Any:
    """Coerce a filter value to what the model field actually holds.

    ``filters`` is typed ``dict[str, str]``, so a bool field is
    addressed as ``"true"``; comparing that to ``True`` never matches.
    An unrecognised token is rejected rather than compared as a string,
    which would report "nothing matched" for a typo.
    """
    if isinstance(value, str) and _admits_bool(annotation):
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        msg = (
            f"Filter '{key}' takes a boolean, got {value!r}. Use one of: "
            f"{', '.join(sorted(_BOOL_TRUE | _BOOL_FALSE))}."
        )
        raise ExpectedToolError(msg)
    return value


def _path_resolves(item: t.Any, path: str) -> bool:
    """Whether ``path``'s ``__``-separated segments resolve on ``item``.

    ``None`` ends the walk only at an INTERMEDIATE segment, where there
    is genuinely nothing to traverse into. On the terminal segment it is
    an ordinary value -- tmux leaves many format fields empty, so
    ``active_pane__pane_start_command`` is None on every shell pane --
    and treating that as unresolvable turns a true empty result into a
    false error.
    """
    current = item
    segments = path.split("__")
    last = len(segments) - 1
    for i, segment in enumerate(segments):
        try:
            current = getattr(current, segment)
        except Exception:  # noqa: BLE001 - any failure means "no such path"
            return False
        if current is None:
            return i == last
    return True


def _attribute_access_error(probe: list[t.Any], field: str) -> str | None:
    """Message if ``field`` raises on every probed item, else ``None``.

    libtmux keeps removed properties around so they raise a message
    naming the replacement. ``dir()`` still lists them, so they reach
    callers as filterable; ``QueryList`` swallows the raise and answers
    an empty list. Surfacing libtmux's own message is what makes the
    refusal useful.

    One item settles it: the raise comes from the class, so it cannot
    differ per instance.
    """
    if not probe:
        return None
    try:
        getattr(probe[0], field)
    except Exception as exc:  # noqa: BLE001 - reported, not handled
        return str(exc)
    return None


def _unknown_field_message(
    key: str,
    field: str,
    allowed_fields: frozenset[str],
    model_fields: t.Mapping[str, t.Any],
    obj_type: type,
) -> str:
    """Build the error for a filter key naming no known field."""
    msg = f"Unknown filter field '{field}' in '{key}'."
    known = sorted(set(allowed_fields) | set(model_fields))
    close = difflib.get_close_matches(field, known, n=3)
    if close:
        msg += f" Did you mean: {', '.join(close)}?"
    return (
        f"{msg} Every field this tool returns is filterable: "
        f"{', '.join(sorted(model_fields))}. libtmux "
        f"{obj_type.__name__} attributes are accepted too, though tmux "
        "leaves many of them empty."
    )


def _raise_if_path_unresolvable(
    probe: list[t.Any],
    field_path: str,
    key: str,
    valid_ops: list[str],
    *,
    operator_parsed: bool,
) -> None:
    """Reject a multi-segment path no item can resolve.

    Guards the traversal fallback: without this, a mistyped operator
    (``session_name__containss``) reads as a path, resolves on nothing
    and filters every row out -- the silent-empty answer this module
    exists to prevent. Only provable when something is there to probe,
    so an empty list is left alone.
    """
    if "__" not in field_path:
        return
    if not probe or any(_path_resolves(item, field_path) for item in probe):
        return
    msg = f"Filter '{key}' names no attribute path on any item."
    if not operator_parsed:
        # Only a key with no operator can be a mistyped one. When an
        # operator WAS parsed off, blaming the last path segment for
        # not being one denies the operator the caller supplied.
        trailing = field_path.rsplit("__", 1)[1]
        close = difflib.get_close_matches(trailing, valid_ops, n=3)
        msg += (
            f" '{trailing}' is not a filter operator either; did you mean '{close[0]}'?"
            if close
            else f" '{trailing}' is not a filter operator either."
        )
    raise ExpectedToolError(msg)


def _apply_filters(
    items: t.Any,
    filters: dict[str, str] | str | None,
    serializer: t.Callable[..., M],
    obj_type: type,
    model_type: type[BaseModel],
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
    obj_type : type
        libtmux class of the filtered items, used to validate filter
        field names. Taken as a parameter rather than read off the
        first item so an empty list still validates -- an empty result
        is exactly when a typo most needs reporting.
    model_type : type
        Model ``serializer`` returns. Its fields are filterable too, so
        that filtering by what a listing displayed always works.

    Returns
    -------
    list
        Serialized list of matching items.

    Raises
    ------
    ExpectedToolError
        If a filter key uses an invalid lookup operator or names a
        field the object cannot have.
    """
    coerced = _coerce_dict_arg("filters", filters)
    if not coerced:
        return [serializer(item) for item in items]
    filters = coerced

    valid_ops = sorted(LOOKUP_NAME_MAP.keys())
    allowed_fields = _filterable_fields(obj_type)
    model_fields = model_type.model_fields
    attr_filters: dict[str, t.Any] = {}
    model_filters: dict[str, t.Any] = {}
    probe = list(items)

    for key, value in filters.items():
        # A trailing segment that is not an operator is part of the
        # attribute path, matching QueryList: it treats an unknown
        # trailing segment as a path and defaults the operator to
        # ``exact``, so ``active_pane__pane_id`` traverses.
        field_path, op = key, ""
        if "__" in key:
            lhs, trailing = key.rsplit("__", 1)
            if trailing in LOOKUP_NAME_MAP:
                field_path, op = lhs, trailing

        field = field_path.split("__", 1)[0]
        if field in allowed_fields:
            removed = _attribute_access_error(probe, field)
            if removed is not None:
                msg = f"Filter field '{field}' cannot be read: {removed}"
                raise ExpectedToolError(msg)
            _raise_if_path_unresolvable(
                probe, field_path, key, valid_ops, operator_parsed=bool(op)
            )
            attr_filters[key] = value
        elif field in _MODEL_FIELD_ALIASES:
            attr_filters[_MODEL_FIELD_ALIASES[field] + key[len(field) :]] = value
        elif field in model_fields:
            annotation = model_fields[field].annotation
            if _admits_bool(annotation) and op and op not in _BOOL_OPERATORS:
                msg = (
                    f"Operator '{op}' does not apply to boolean field "
                    f"'{field}'. Use {' or '.join(sorted(_BOOL_OPERATORS))}, "
                    "or omit the operator."
                )
                raise ExpectedToolError(msg)
            # Computed server-side, so it exists only after serializing.
            model_filters[key] = _coerce_model_value(key, value, annotation)
        else:
            raise ExpectedToolError(
                _unknown_field_message(
                    key, field, allowed_fields, model_fields, obj_type
                )
            )

    filtered = items.filter(**attr_filters) if attr_filters else items
    results = [serializer(item) for item in filtered]
    if model_filters:
        results = list(QueryList(results).filter(**model_filters))
    return results


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
    active_pane = getattr(window, "active_pane", None)
    active_pane_id = active_pane.pane_id if active_pane is not None else None

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
        active_pane_id=active_pane_id,
    )


def _coerce_int(value: str | None) -> int | None:
    """Parse a tmux format-string number into ``int`` or ``None``.

    tmux format variables come back as strings; an empty string means
    "tmux returned nothing" (e.g. older tmux that doesn't know the var).
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: str | None) -> bool | None:
    """Parse a tmux ``"1"``/``"0"`` flag into ``bool`` or ``None``.

    Mirrors libtmux's own ``Pane.at_top`` / ``at_bottom`` typing, which
    folds ``"1"`` to True and everything else to False — except we keep
    ``None`` distinct so callers can tell "tmux didn't tell us" from
    "tmux said no".
    """
    if value is None or value == "":
        return None
    return value == "1"


def _serialize_pane(pane: Pane) -> PaneInfo:
    """Serialize a Pane to a Pydantic model.

    Parameters
    ----------
    pane : Pane
        The pane to serialize.

    Returns
    -------
    PaneInfo
        Pane data including id, dimensions, geometry, current command, title.
    """
    from libtmux_mcp.models import PaneInfo

    assert pane.pane_id is not None
    return PaneInfo(
        pane_id=pane.pane_id,
        pane_index=getattr(pane, "pane_index", None),
        pane_width=getattr(pane, "pane_width", None),
        pane_height=getattr(pane, "pane_height", None),
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
        pane_pid=getattr(pane, "pane_pid", None),
        pane_title=getattr(pane, "pane_title", None),
        pane_active=getattr(pane, "pane_active", None),
        window_id=pane.window_id,
        session_id=pane.session_id,
        is_caller=_compute_is_caller(pane),
    )


P = t.ParamSpec("P")
R = t.TypeVar("R")


#: tmux stderr fragments that mean the socket genuinely has no daemon
#: behind it. Anything else on a failed ``list-sessions`` -- a protocol
#: mismatch, a permission error -- means a server that exists and cannot
#: be talked to, which is a different answer.
_NO_SERVER_MARKERS = (
    "no server running",
    "no such file or directory",
    "error connecting to",
)


def _probe_liveness(server: Server) -> tuple[bool, str | None]:
    """Return ``(alive, unreachable_reason)`` for *server*.

    ``Server.is_alive()`` answers False for a socket with no daemon AND
    for a live server this tmux binary cannot speak to, and
    ``Server.sessions`` degrades to ``[]`` in both cases. libtmux's own
    docstring points at ``is_alive`` to tell those apart, but it cannot:
    both collapse to the same False.

    The difference matters because they warrant opposite reactions. "No
    server" is a fact an agent can act on; "cannot reach the server" over
    a socket whose daemon is running -- an ordinary tmux upgrade leaves
    sockets older than the binary -- reported as False tells the agent
    the user's work is gone. tmux distinguishes them on stderr, so read
    it rather than the boolean.
    """
    try:
        result = server.cmd("list-sessions")
    except Exception as err:  # noqa: BLE001 - probe must not raise
        return False, str(err)

    if result.returncode == 0:
        return True, None

    detail = " ".join(result.stderr).strip() if result.stderr else ""
    lowered = detail.lower()
    if any(marker in lowered for marker in _NO_SERVER_MARKERS):
        return False, None
    return False, detail or f"tmux exited with status {result.returncode}"


def _undouble(prefix: str, text: str) -> str:
    """Drop *prefix* from *text* when the wrapper is about to add it back."""
    return text.removeprefix(prefix)


def _is_format_newline_parse_error(e: BaseException) -> bool:
    """Detect libtmux failing to parse a format value containing a newline.

    libtmux <= 0.62.0 splits ``-F`` output one line per object, so a
    newline inside any value (a pane's current directory, most reachably)
    splits that record and its strict ``zip`` raises. It surfaces as a
    bare ``ValueError`` and would otherwise reach the agent as
    "Unexpected error", logged at ERROR, naming nothing it can act on.

    Matched on the message because the raise site is a stdlib ``zip``
    with no dedicated exception type. Kept even once the floor moves
    past the libtmux fix: the installed version is not ours to choose.
    """
    return isinstance(e, ValueError) and "zip()" in str(e)


def _map_exception_to_tool_error(fn_name: str, e: BaseException) -> ToolError:
    """Translate a libtmux / unexpected exception into a ``ToolError``.

    Shared between the sync and async ``handle_tool_errors*`` decorators
    so the two paths stay byte-for-byte identical in what agents see.

    Expected, agent-correctable failures map to
    :class:`ExpectedToolError` (logged at WARNING). Two cases stay at
    ERROR: a missing tmux binary (operator-environment fault that must
    be loud) and the unexpected catch-all (potential bug in this
    server).
    """
    if isinstance(e, exc.TmuxCommandNotFound):
        msg = "tmux binary not found. Ensure tmux is installed and in PATH."
        return ToolError(msg)
    if isinstance(e, exc.TmuxSessionExists):
        return ExpectedToolError(str(e))
    if isinstance(e, exc.BadSessionName):
        return ExpectedToolError(str(e))
    if isinstance(e, exc.ObjectDoesNotExist):
        return ExpectedToolError(
            f"Object not found: {e}",
            suggestion=(
                "Call list_sessions / list_windows / list_panes to discover valid ids."
            ),
        )
    if isinstance(e, exc.MultipleObjectsReturned):
        return ExpectedToolError(
            f"Ambiguous target: {e}",
            suggestion=(
                "A window shared between sessions is listed once per session that "
                "holds it, so a name or index can match more than one row. Target "
                "it by id (session_id / window_id / pane_id) instead."
            ),
        )
    if isinstance(e, exc.PaneNotFound):
        return ExpectedToolError(
            f"Pane not found: {_undouble('Pane not found: ', str(e))}",
            suggestion="Call list_panes to discover valid pane ids.",
        )
    if _is_format_newline_parse_error(e):
        return ExpectedToolError(
            "tmux listing could not be parsed: a format value contains a "
            "newline, almost always a pane whose current directory has one "
            "in its name. Every pane on this server is affected, not just "
            "that one, because pane lookup enumerates them all.",
            suggestion=(
                "Find it with: tmux list-panes -a -F "
                "'#{pane_id} #{pane_current_path}' | cat -A — then move or "
                "rename that directory. Upgrading libtmux also fixes it."
            ),
        )
    if isinstance(e, exc.LibTmuxException):
        return ExpectedToolError(f"tmux error: {e}")
    logger.exception("unexpected error in MCP tool %s", fn_name)
    return ToolError(f"Unexpected error: {type(e).__name__}: {e}")


def handle_tool_errors(
    fn: t.Callable[P, R],
) -> t.Callable[P, R]:
    """Decorate synchronous MCP tool functions with standardized error handling.

    Catches libtmux exceptions and re-raises them through
    :func:`_map_exception_to_tool_error` so MCP responses have
    ``isError=True`` with a descriptive message — expected,
    agent-correctable failures as :class:`ExpectedToolError` (logged
    at WARNING), the unexpected catch-all as stock ``ToolError``
    (logged at ERROR).

    The re-raise chains the original exception via ``from e``. Keep it
    single-level: :class:`~libtmux_mcp.middleware.ReadonlyRetryMiddleware`
    matches :exc:`libtmux.exc.LibTmuxException` by inspecting exactly
    one ``__cause__`` hop, so wrapping the mapped error again would
    silently disable readonly retries.

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

    Maps the same libtmux exception set to the same messages and
    error classes as the sync decorator (expected failures as
    :class:`ExpectedToolError` at WARNING, the unexpected catch-all as
    stock ``ToolError`` at ERROR) by delegating to a shared helper,
    and chains the original exception via the same single-level
    ``from e`` that readonly retries depend on.
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
