"""Which tmux pane, if any, the caller is talking to us from.

Discovery tools mark the caller's own pane, and the destructive tools
refuse to kill it. `$TMUX` names only the innermost server, so identity
is settled by who is attached rather than by how the nesting arose.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import pathlib
import typing as t

from libtmux import exc
from libtmux.server import Server

if t.TYPE_CHECKING:
    from libtmux.pane import Pane


from libtmux_mcp._exec import _LIVENESS_TIMEOUT_SECONDS, _run_tmux_sync

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
    # ``display-message -p`` prints and exits, so this is cheap. Wrapped
    # because the server may be down, the format unsupported on an old
    # tmux, or the call denied.
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


def _target_hosts_the_callers_client(server: Server, caller: CallerIdentity) -> bool:
    """Whether a pane on *server* is the terminal the caller lives in.

    ``$TMUX`` names only the INNERMOST server. Run an agent inside tmux,
    point it at a second tmux, and its ``$TMUX`` describes the inner one
    while the pane actually hosting its terminal belongs to the outer
    one -- so every path comparison above says "different server" and a
    kill of that pane is permitted. It takes the caller's tty with it,
    which is the self-kill this guard exists to prevent. Reproduced on
    3.7c: guard vs the inner server True, vs the outer server False.

    Asking WHO IS ATTACHED answers it without caring how the nesting
    arose. A client of the caller's own server occupies a pane of
    whatever hosts it, so the inner server's ``client_tty`` is the outer
    server's ``pane_tty`` -- measured, both ``/dev/pts/50``. Walking the
    process tree instead would only find servers STARTED FROM a pane,
    missing one merely attached to, and would need ``/proc``, which
    macOS does not have.

    A HUNG probe fails closed, matching the bias of the table above. A
    nonzero exit does not: "no such server" is an answer -- a server
    that is gone hosts nothing -- and treating it as unknown would block
    every destructive call for a caller whose ``$TMUX`` names a socket
    that has since died. An empty client list is an answer for the same
    reason.
    """
    caller_server = Server(socket_path=caller.socket_path)
    attached = _run_tmux_sync(
        caller_server,
        "list-clients",
        "-F",
        "#{client_tty}",
        timeout=_LIVENESS_TIMEOUT_SECONDS,
    )
    if attached is None:
        return True
    if attached.returncode != 0:
        # No such server is an ANSWER, not a failure: it cannot be
        # hosting us. Distinct from the timeout above, where a wedged
        # server might be -- so a dead $TMUX socket cannot block every
        # destructive call.
        return False
    ttys = {line.strip() for line in attached.stdout.splitlines() if line.strip()}
    if not ttys:
        return False
    panes = _run_tmux_sync(
        server,
        "list-panes",
        "-a",
        "-F",
        "#{pane_tty}",
        timeout=_LIVENESS_TIMEOUT_SECONDS,
    )
    if panes is None:
        return True
    if panes.returncode != 0:
        return False
    return any(line.strip() in ttys for line in panes.stdout.splitlines())


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
    * a pane on the target server is the terminal the caller's own
      server is attached through (nested tmux) → ``True``. ``$TMUX``
      names only the innermost server, so the comparisons above cannot
      see this one.
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
    if caller_basename == target_name:
        return True
    # Nesting: $TMUX names only the innermost server, so none of the
    # comparisons above can see a pane on ANOTHER server that is hosting
    # this one.
    return _target_hosts_the_callers_client(server, caller)


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
