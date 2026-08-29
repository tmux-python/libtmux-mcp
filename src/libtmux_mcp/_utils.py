"""Shared utilities for libtmux MCP server.

Provides server caching, object resolution, serialization, and error handling
for all MCP tool functions.
"""

from __future__ import annotations

import dataclasses
import difflib
import errno
import functools
import importlib
import json
import logging
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import threading
import typing as t

from fastmcp.exceptions import ToolError
from libtmux import exc
from libtmux._internal.query_list import LOOKUP_NAME_MAP, QueryList
from libtmux.common import tmux_cmd
from libtmux.server import Server

from libtmux_mcp._tmux_proc import _run_tmux_bounded as _run_tmux_async

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


# ---------------------------------------------------------------------------
# Safety tier tags
# ---------------------------------------------------------------------------

TAG_READONLY = "readonly"
TAG_MUTATING = "mutating"
TAG_DESTRUCTIVE = "destructive"

VALID_SAFETY_LEVELS = frozenset({TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE})

#: Non-tier marker for tools that enforce their own wall-clock ceiling,
#: whose cost is therefore *duration* rather than side effects. Such a
#: tool must never be re-driven by machinery that assumes a cheap call:
#:
#: * :class:`~libtmux_mcp.middleware.ReadonlyRetryMiddleware` skips it --
#:   the deadline lives in the tool body, so a retry doubles the ceiling.
#: * The ``call_*_tools_batch`` wrappers reject it per-operation: the
#:   batch loop is serial with no aggregate deadline and
#:   ``MAX_BATCH_OPERATIONS`` is 1000.
#:
#: A tag rather than a name list because ``add_tool_transformation`` can
#: rename a tool out from under a name. Tier resolution reads only the
#: three tier tags, so this one is inert elsewhere.
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
#: Annotations for tools that move a user-supplied payload into a shell
#: context, whether directly (``send_keys``, ``run_command``,
#: ``paste_text``, ``pipe_pane``) or through a staged buffer
#: (``load_buffer`` then ``paste_buffer``).
#:
#: ``openWorldHint=True`` is what separates these from
#: :data:`ANNOTATIONS_CREATE`: the effect extends into whatever command
#: or content the caller supplies.
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

#: Per-tool MCP ``meta`` hinting that a client keep this tool visible
#: rather than deferred. FastMCP passes ``meta`` opaquely and honouring it
#: is the client's business, so this is a safe no-op for one that does not
#: index the ``anthropic/*`` namespace. ``alwaysLoad`` is documented at
#: https://code.claude.com/docs/en/mcp, honoured from Claude Code 2.1.121.
#:
#: Apply only to read-tier discovery anchors -- ``list_panes``,
#: ``list_windows``, ``snapshot_pane`` -- because each always-loaded tool
#: spends a fixed schema budget in clients that do honour the hint.
DISCOVERY_META: dict[str, t.Any] = {
    "anthropic/alwaysLoad": True,
}
#: Annotations for tools that stay in the ``mutating`` tier -- so they
#: remain visible to default-profile agents -- but can still terminate a
#: process or lose state. ``respawn_pane`` and ``clear_pane`` are the
#: canonical users: shell recovery and scrollback cleanup are ordinary
#: agent work, while the hints keep disclosing the cost.
#:
#: Hint values match :data:`ANNOTATIONS_DESTRUCTIVE`, which is paired with
#: ``TAG_DESTRUCTIVE`` where this one is paired with ``TAG_MUTATING``. Two
#: names for identical hints, so the call site states which it means.
ANNOTATIONS_MUTATING_DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}


#: POSIX portable environment variable name.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _raise_if_untargeted(tool: str, **targets: str | None) -> None:
    """Refuse a call that delivers input without saying where.

    Reads may default; a tool that types into a pane may not. The
    default was the first LISTED object, which tmux orders by name, so
    ``rename_session`` moved where an untargeted ``send_keys`` landed --
    keystrokes into a pane belonging to a session the caller had never
    touched. Keying the default on the tmux id makes it stable, but
    stable is not the same as correct: nothing about the call says
    which pane was meant.

    The precedent is in this same server. ``kill_window`` requires
    ``window_id``, so the destructive tools already refuse to guess.
    There is no principled reason ``send_keys`` gets to, and it is the
    one that executes something.

    The destination is disclosed in the result today, which is not the
    same as a guard: it arrives after the keystrokes have landed.
    """
    if any(value is not None for value in targets.values()):
        return
    msg = (
        f"{tool} requires an explicit target: pass "
        f"{', '.join(sorted(targets))}. It delivers input to a pane, so "
        "there is no safe default -- the pane it would have picked "
        "belongs to whichever session is oldest, which is unrelated to "
        "what the call is for. Use list_panes or search_panes to find "
        "the pane, and snapshot_pane to confirm what it is running."
    )
    raise ExpectedToolError(msg)


#: Linux caps one argv element at 32 pages regardless of total argv
#: size. Reported in the error rather than enforced: the OS is the
#: authority, and predicting it would drift from the platform.
_MAX_ARG_STRLEN = 131072


def _raise_tmux_exec_error(err: OSError, argv: list[str]) -> t.NoReturn:
    """Re-raise an exec-time ``OSError`` as a caller-correctable failure.

    Every ``subprocess.run`` here catches ``TimeoutExpired`` and
    ``CalledProcessError`` -- both of which mean tmux RAN. An argv that
    never reaches tmux fails earlier and differently, and the raw
    ``OSError`` then surfaced as "Unexpected error", which reads as a
    server defect rather than as oversized input.

    ``E2BIG`` is the one an agent can hit with ordinary input: Linux
    caps a SINGLE argv element at ``MAX_ARG_STRLEN`` (32 pages, 131072
    bytes), independently of the total. Measured, the boundary is
    exact -- 131071 bytes reaches tmux and is rejected with ``command
    too long``, 131072 fails in ``execve``. Both mean "too big", but
    only one of them used to say so.

    Parameters
    ----------
    err : OSError
        The exec failure.
    argv : list of str
        The command vector, used to report which argument was too long.

    Raises
    ------
    ExpectedToolError
        For a failure the caller can correct.
    OSError
        Re-raised unchanged when it is not one of those.
    """
    if err.errno == errno.E2BIG:
        longest = max((len(a) for a in argv), default=0)
        msg = (
            f"tmux {_tmux_subcommand(argv)} argument is too large to pass to "
            f"the OS ({longest} bytes; the limit is {_MAX_ARG_STRLEN} per "
            "argument). Use paste_text, which routes through a tmux buffer "
            "instead of argv and has no comparable limit."
        )
        raise ExpectedToolError(msg) from err
    if isinstance(err, FileNotFoundError):
        raise exc.TmuxCommandNotFound from err
    raise err


def _raise_if_flag_like(label: str, value: str) -> None:
    """Refuse a caller string tmux would parse as a flag.

    tmux reads flags before quoting can protect anything, and libtmux
    emits ``[name, value]`` with no ``--`` terminator. So a leading
    ``-`` substitutes one command for another silently: measured,
    ``set_environment(name="-u", value="VICTIM")`` UNSET ``VICTIM`` and
    reported ``status="set"``, and ``set_option(option="-g", value="x")``
    turned off ``xterm-keys`` because tmux prefix-matched ``x``.
    """
    if value.startswith("-"):
        msg = (
            f"{label} may not begin with '-': tmux parses it as a flag, so "
            f"the call would run a different command than the one requested "
            f"(got {value!r})."
        )
        raise ExpectedToolError(msg)


def _raise_if_not_env_name(name: str) -> None:
    """Refuse an environment variable name tmux or POSIX cannot hold."""
    if not _ENV_NAME_RE.match(name):
        msg = (
            f"Environment variable name must match [A-Za-z_][A-Za-z0-9_]* "
            f"(got {name!r}). tmux stores anything else verbatim as an "
            "unusable name, and a leading '-' is read as a flag."
        )
        raise ExpectedToolError(msg)


#: Characters that make a spawn command a shell PROGRAM rather than a bare
#: invocation. tmux hands a one-argument command to ``$SHELL -c``
#: (``spawn.c``: "If one argument, pass it to $SHELL -c"), so anything sh
#: interprets is beyond a pre-flight's reach.
_SHELL_METACHARACTERS = frozenset(";&|<>()$`\\\"'\n\t*?[]{}~#=!")

#: Words that legitimately begin a command and are never found on PATH.
#: Without these the pre-flight refuses ``exec sleep 60`` and ``cd /tmp``,
#: which sh runs perfectly well.
_SHELL_BUILTINS = frozenset(
    (
        ".",
        ":",
        "alias",
        "bg",
        "break",
        "case",
        "cd",
        "command",
        "continue",
        "do",
        "done",
        "elif",
        "else",
        "esac",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "fc",
        "fg",
        "fi",
        "for",
        "function",
        "getopts",
        "hash",
        "if",
        "in",
        "jobs",
        "kill",
        "local",
        "newgrp",
        "pwd",
        "read",
        "readonly",
        "return",
        "select",
        "set",
        "shift",
        "source",
        "test",
        "then",
        "time",
        "times",
        "trap",
        "true",
        "type",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "until",
        "wait",
        "while",
    )
)


def _unrunnable_spawn_program(shell: str) -> str | None:
    """Return the program tmux certainly cannot run, else ``None``.

    ``None`` covers both "this will run" and "no pre-flight can tell",
    and the two are deliberately not distinguished: the only safe
    refusal is one that cannot be wrong.

    Anything sh interprets is undecidable, because tmux passes a
    one-argument command to ``$SHELL -c`` rather than exec'ing it.
    Measured: ``cd /tmp && sleep 60``, ``VAR=1 sleep 60`` and
    ``exec sleep 60`` all run, and an earlier version of this check
    refused all three while asserting the pane would die.
    """
    if _SHELL_METACHARACTERS & set(shell):
        return None
    try:
        program = shlex.split(shell)[0]
    except (ValueError, IndexError):
        return None
    if program in _SHELL_BUILTINS:
        return None
    if "/" in program:
        return None if os.access(program, os.X_OK) else program
    return None if shutil.which(program) is not None else program


def _raise_if_shell_unrunnable(shell: str | None, *, consequence: str) -> None:
    """Refuse a spawn command whose program cannot be executed.

    Checked BEFORE spawning because the failure is destructive rather
    than merely wrong: tmux reports success, the new process dies, and
    the pane goes with it. Catching it afterwards can only report the
    loss, and even that races the doomed process.
    """
    if not shell:
        return
    program = _unrunnable_spawn_program(shell)
    if program is None:
        return
    msg = f"{program!r} is not an executable command. {consequence}"
    raise ExpectedToolError(msg)


def _raise_if_start_directory_unusable(start_directory: str | None) -> None:
    """Refuse a start directory the spawned pane could not actually use.

    tmux never reports this. ``spawn.c`` tries ``chdir(cwd)``, then
    ``chdir($HOME)``, then ``chdir("/")``, and succeeds either way -- so
    a typo, a flag-shaped value or an unexpanded ``~`` puts the pane in
    the home directory while the caller is told otherwise. Measured on
    ``create_session``, ``split_window`` and ``create_window``: six
    unusable values, zero errors, every pane in ``$HOME``.

    ``None`` means "not specified" and inherits normally. An empty
    string does not: tmux then takes the client's cwd, which is the MCP
    server's own working directory and has nothing to do with the
    caller.
    """
    if start_directory is None:
        return
    if (
        start_directory
        and pathlib.Path(start_directory).is_dir()
        and os.access(start_directory, os.X_OK)
    ):
        return
    expanded = str(pathlib.Path(start_directory).expanduser())
    if expanded != start_directory and pathlib.Path(expanded).is_dir():
        hint = f" tmux does not expand '~' -- pass {expanded!r}."
    elif not start_directory:
        hint = (
            " An empty string is not the same as omitting the argument: "
            "tmux would use the MCP server's own working directory."
        )
    else:
        hint = ""
    msg = (
        f"start_directory {start_directory!r} is not a usable directory. "
        f"tmux reports no error for this -- it falls back to $HOME, then "
        f"to '/', so the pane would start somewhere that was never "
        f"requested.{hint}"
    )
    raise ExpectedToolError(msg)


def _raise_spawned_pane_gone(shell: str | None) -> t.NoReturn:
    """Report a spawn that tmux accepted and then had nothing to show for."""
    detail = f" running {shell!r}" if shell else ""
    msg = (
        f"The new pane{detail} exited immediately and tmux removed it, so "
        "there is no pane to return. tmux reports a split like this as "
        "successful."
    )
    raise ExpectedToolError(msg) from None


def _raise_if_spawned_pane_is_gone(pane: Pane, shell: str | None) -> None:
    """Refuse to report a pane the spawn has already destroyed.

    The pre-flight cannot cover this on its own: anything sh interprets
    is undecidable in advance, and ``#{session_name}`` reaches sh as a
    comment, so the pane exits 0 and disappears. tmux still reports
    success. Measured: ``refresh()`` raises at t+0, so the vanished pane
    is observable immediately rather than racily.
    """
    try:
        pane.refresh()
    except exc.TmuxObjectDoesNotExist:
        _raise_spawned_pane_gone(shell)


#: How long any single tmux call may take before this server calls the
#: tmux server unresponsive. Generous: a responsive tmux answers in
#: single-digit milliseconds, and capturing a 200,000-line history --
#: the slowest operation constructible here -- measured 37ms.
#:
#: THE one definition of that policy. Every other bound derives from it
#: rather than repeating the literal, because a second copy does not
#: fail when this one moves; the tools just start disagreeing.
_LIVENESS_TIMEOUT_SECONDS = 5.0

#: Distinguishable by identity, so callers can tell "did not answer"
#: from every other unreachable reason without matching on prose.
HUNG_SOCKET_REASON = (
    "the tmux server accepted the connection but did not answer within "
    f"{_LIVENESS_TIMEOUT_SECONDS:g}s"
)


def _run_tmux_sync(
    server: Server, *tmux_args: str, timeout: float
) -> subprocess.CompletedProcess[str] | None:
    """Run one tmux command with a hard bound, or ``None`` if it hung.

    SYNCHRONOUS, and named so: the async path must not use this or a
    thread wrapper around it. See :mod:`libtmux_mcp._tmux_proc`.

    A socket with a listener is not a server that answers. A tmux server
    spinning inside its own event loop accepts the connection and never
    replies, and ``Server.cmd`` has no timeout -- so ONE such socket in
    ``$TMUX_TMPDIR`` made ``list_servers`` never return. Measured: the
    same scan took 2.03s before a silent listener was added to the
    directory and had not finished 85 seconds after.

    The child is killed on timeout, so an abandoned probe does not leave
    a tmux client behind.
    """
    try:
        return subprocess.run(
            _tmux_argv(server, *tmux_args),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


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


def _server_cache_key(
    socket_name: str | None, socket_path: str | None
) -> tuple[str | None, str | None, str | None]:
    """Cache key with the environment fallbacks already applied."""
    if socket_name is None:
        socket_name = os.environ.get("LIBTMUX_SOCKET")
    if socket_path is None:
        socket_path = os.environ.get("LIBTMUX_SOCKET_PATH")
    return (socket_name, socket_path, os.environ.get("LIBTMUX_TMUX_BIN"))


#: Hard bound on ONE tmux call made through a synchronous tool. Same
#: value as the liveness probe's budget, and safe to be wrong for the
#: same reason: expiry yields a disclosed error naming the subcommand,
#: never a confident wrong answer, so a too-small value cannot mislead.
_SYNC_CALL_TIMEOUT_SECONDS = _LIVENESS_TIMEOUT_SECONDS


#: Stock ``tmux_cmd`` logs on this logger and callers read dispatched
#: argv off its records, so the bounded replacement must use it too
#: rather than its own module logger.
_LIBTMUX_COMMON_LOGGER = logging.getLogger("libtmux.common")


def _tmux_subcommand(argv: list[str]) -> str:
    """Name the tmux subcommand in an argv, for error messages.

    ``argv[1]`` may be ``-Lname``: libtmux joins the socket flag to its
    value, so the first non-flag element is the subcommand.
    """
    for part in argv[1:]:
        if not part.startswith("-"):
            return part
    return "command"


class _BoundedTmuxCmd(tmux_cmd):
    """A ``tmux_cmd`` that cannot outlive its timeout.

    libtmux runs every tmux command through an untimed
    ``Popen.communicate()`` (``libtmux/common.py``), so a server that
    accepts the connection and then says nothing hangs its caller
    forever. A tool's liveness probe bounds only the FIRST round trip:
    ``break_pane`` makes eleven and held for 150s at the second.

    Bounding at ``tmux_cmd`` rather than ``Server.cmd`` is deliberate.
    ``Server.cmd`` is not the only funnel -- ``neo.fetch_objs`` builds a
    ``tmux_cmd`` directly, and that is the path behind ``window.panes``
    and ``session.windows``, so a ``Server`` subclass leaves the busiest
    caller unbounded. See :func:`_install_bounded_tmux_cmd`.
    """

    def __init__(self, *args: t.Any, tmux_bin: str | None = None) -> None:
        resolved = tmux_bin or shutil.which("tmux")
        if not resolved:
            raise exc.TmuxCommandNotFound
        argv = [resolved, *(str(arg) for arg in args)]
        self.cmd = argv
        # A contract, not decoration: callers read the argv of every
        # dispatched command out of these two records, so replacing
        # __init__ without them blinds that silently.
        emit_debug = _LIBTMUX_COMMON_LOGGER.isEnabledFor(logging.DEBUG)
        cmd_str = shlex.join(argv) if emit_debug else ""
        if emit_debug:
            _LIBTMUX_COMMON_LOGGER.debug(
                "tmux command dispatched", extra={"tmux_cmd": cmd_str}
            )
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                timeout=_SYNC_CALL_TIMEOUT_SECONDS,
                encoding="utf-8",
                errors="backslashreplace",
            )
        except FileNotFoundError:
            raise exc.TmuxCommandNotFound from None
        except subprocess.TimeoutExpired as err:
            # NOT a LibTmuxException: Server._fetch_or_empty catches those
            # and returns [] for a not-yet-started daemon, which would
            # report a WEDGED server as having no sessions. subprocess.run
            # kills and reaps before raising, so no tmux client is left.
            msg = (
                f"tmux {_tmux_subcommand(argv)} did not return within "
                f"{_SYNC_CALL_TIMEOUT_SECONDS:.2f}s; the tmux server is unresponsive"
            )
            raise ExpectedToolError(msg) from err

        self.returncode = completed.returncode
        stdout_split = (completed.stdout or "").split("\n")
        while stdout_split and stdout_split[-1] == "":
            stdout_split.pop()
        self.stderr = list(filter(None, (completed.stderr or "").split("\n")))
        # libtmux surfaces has-session's failure through stdout; mirrored
        # so Server.has_session reads the same either way.
        if "has-session" in argv and self.stderr and not stdout_split:
            self.stdout = [self.stderr[0]]
        else:
            self.stdout = stdout_split

        if emit_debug:
            _LIBTMUX_COMMON_LOGGER.debug(
                "tmux command completed",
                extra={
                    "tmux_cmd": cmd_str,
                    "tmux_exit_code": self.returncode,
                    "tmux_stdout": self.stdout[:100],
                    "tmux_stderr": self.stderr[:100],
                    "tmux_stdout_len": len(self.stdout),
                    "tmux_stderr_len": len(self.stderr),
                },
            )


#: Every libtmux module that constructs a ``tmux_cmd``. Each resolves the
#: name as a module global at call time, so rebinding it here bounds the
#: call. A test AST-walks the installed libtmux so a new call site in a
#: new module fails loudly rather than reintroducing an unbounded path.
_PATCHED_LIBTMUX_MODULES = ("libtmux.common", "libtmux.neo", "libtmux.server")


def _install_bounded_tmux_cmd() -> None:
    """Point libtmux's ``tmux_cmd`` references at the bounded subclass."""
    for name in _PATCHED_LIBTMUX_MODULES:
        module = importlib.import_module(name)
        if getattr(module, "tmux_cmd", None) is not _BoundedTmuxCmd:
            module.tmux_cmd = _BoundedTmuxCmd  # type: ignore[attr-defined]


_install_bounded_tmux_cmd()


def _build_server(*, socket_name: str | None, socket_path: str | None) -> Server:
    """Construct an unprobed handle, honouring the same env fallbacks."""
    name, path, tmux_bin = _server_cache_key(socket_name, socket_path)
    kwargs: dict[str, t.Any] = {}
    if name is not None:
        kwargs["socket_name"] = name
    if path is not None:
        kwargs["socket_path"] = path
    if tmux_bin is not None:
        kwargs["tmux_bin"] = tmux_bin
    return Server(**kwargs)


def _raise_socket_hung(server: Server) -> t.NoReturn:
    """Report a socket that accepted a connection and then said nothing."""
    target = server.socket_path or server.socket_name or "<default>"
    msg = (
        f"tmux server at {target} accepted the connection but did not "
        f"answer within {_LIVENESS_TIMEOUT_SECONDS:g}s. It is running and "
        "wedged rather than absent, so its sessions are not lost -- but no "
        "tmux command against it can complete until it is killed."
    )
    raise ExpectedToolError(msg)


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
    cache_key = _server_cache_key(socket_name, socket_path)
    with _server_cache_lock:
        cached = _server_cache.get(cache_key)

    # ``is_alive()`` is a tmux subprocess round trip; holding the cache
    # lock across it serialises every concurrent tool call in this
    # process -- measured, a 16-way socket scan capped at 2x, not 8x.
    if cached is not None:
        alive, reason = _probe_liveness(cached)
        _raise_if_socket_hung(cached, reason)
        if alive:
            return cached
        with _server_cache_lock:
            if _server_cache.get(cache_key) is cached:
                del _server_cache[cache_key]

    server = _build_server(socket_name=socket_name, socket_path=socket_path)

    # Probed before it is handed out: nothing downstream is bounded, as
    # ``server.panes`` and friends reach ``Server.cmd``, which has no
    # timeout. One extra round trip on the uncached path; the cached path
    # above already pays one on every call.
    _, reason = _probe_liveness(server)
    _raise_if_socket_hung(server, reason)

    # Two threads racing to fill the same key both build a valid handle;
    # ``setdefault`` makes them agree on which one the cache keeps.
    with _server_cache_lock:
        return _server_cache.setdefault(cache_key, server)


def _raise_if_socket_hung(server: Server, reason: str | None) -> None:
    """Refuse to hand out a server that accepted a connection in silence.

    A DEAD socket is not this: it answers immediately with "no server
    running" and every tool reports it correctly. This is only the
    socket that takes the connection and never replies, where the
    alternative to refusing is blocking a worker until the process ends.
    """
    if reason is not HUNG_SOCKET_REASON:
        return
    _raise_socket_hung(server)


async def _get_server_async(
    socket_name: str | None = None,
    socket_path: str | None = None,
) -> Server:
    """Resolve a server without blocking the event loop.

    ``_get_server`` runs a tmux subprocess to check the socket answers,
    which is ~4 ms against a healthy server and the full liveness bound
    against one that never replies. Called directly from an async tool
    that cost every OTHER in-flight call the same wait: measured, an
    ``capture_since`` against a wedged socket held the loop for 5.01 s
    and the ticker beside it advanced once.

    The blocking predates the bound -- the cached path always shelled
    out -- but a bounded 5 s stall shared by every concurrent caller is
    still a stall, and the async tools are the ones with company.

    An async SUBPROCESS, not ``to_thread``: the wait path forbids
    worker threads outright, because
    ``concurrent.futures.thread._python_exit`` joins them with no
    timeout and one wedged tmux would hang interpreter exit forever.
    A subprocess we own can be killed. See
    :mod:`libtmux_mcp._tmux_proc`.
    """
    server = _build_server(socket_name=socket_name, socket_path=socket_path)
    cache_key = _server_cache_key(socket_name, socket_path)
    with _server_cache_lock:
        cached = _server_cache.get(cache_key)
    probe = cached if cached is not None else server
    returncode = 0
    try:
        returncode, _stdout, _stderr = await _run_tmux_async(
            _tmux_argv(probe, "list-sessions"),
            timeout=_LIVENESS_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _raise_socket_hung(probe)
    except OSError:
        pass  # a missing binary or socket is not a hang; the caller sees it
    if cached is not None:
        if returncode == 0:
            return cached
        # Matches the synchronous path: a handle whose server is gone is
        # dropped rather than reused. Diverging here would mean the same
        # socket answered differently depending on which tool asked.
        with _server_cache_lock:
            if _server_cache.get(cache_key) is cached:
                del _server_cache[cache_key]
    with _server_cache_lock:
        return _server_cache.setdefault(cache_key, server)


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


def _raise_if_server_unreachable(server: Server) -> None:
    """Refuse to read an empty enumeration as an absence.

    ``server.sessions`` swallows a query failure and yields an empty
    list, so a resolver turning "not in the list" into "does not exist"
    asserts the object is GONE when the truth is that the server could
    not be asked. Measured against a live 3.7c server queried by a 3.2a
    client: ``rename_session`` reported the session missing while it was
    running, which invites recreating it under the same name.

    Only the session resolver needed this. Resolvers keyed on
    ``pane_id`` or ``window_id`` let tmux's own error through, which is
    untidy but never false -- they are the ones already telling the
    truth.

    Also covers the opposite end. ``_probe_liveness`` separates "no
    server" from "unreachable", and a missing server reaching the
    object-not-found path produced advice that cannot work: it tells the
    caller to run ``list_sessions``, which fails identically. Both
    branches raise here so neither answer is a guess.
    """
    alive, reason = _probe_liveness(server)
    if alive:
        return
    if reason is not None:
        msg = (
            f"tmux server exists but could not be queried: {reason}. "
            "Reporting the object as missing would be wrong rather than "
            "merely unhelpful."
        )
        raise ExpectedToolError(msg)
    # No server at all is not "that object is missing": the
    # object-not-found path advises list_sessions, which fails the same
    # way here and sends the caller round the loop it is already in.
    socket = getattr(server, "socket_name", None) or getattr(
        server, "socket_path", None
    )
    msg = f"no tmux server is running{f' on {socket}' if socket else ''}"
    raise ExpectedToolError(
        msg,
        suggestion=(
            "There is no enumeration to consult. create_session starts a "
            "server and a session in one call; list_servers finds sockets "
            "that already have one."
        ),
    )


def tmux_id_sort_key(raw: str | None) -> tuple[int, str]:
    """Sort key placing tmux ids in creation order.

    ``$10`` is newer than ``$9``; a string sort says otherwise, and only
    once ids pass nine -- on a long-lived server, which is exactly where
    it would go unnoticed longest.
    """
    text = raw or ""
    digits = text[1:] if text[:1] in "$@%" else text
    return (int(digits), text) if digits.isdigit() else (2**62, text)


def _oldest(objects: list[t.Any], id_field: str) -> t.Any:
    """Return the object with the lowest tmux id, oldest surviving first.

    The untargeted default has to key on something a later call cannot
    move. It used to be list order, and tmux lists sessions BY NAME --
    so ``rename_session`` silently redirected every later untargeted
    call into a DIFFERENT session's pane, and nothing about that session
    had changed. tmux's own rule for an omitted ``-t`` is no better: it
    picks by ``activity_time``, which moves whenever any pane produces
    output.

    tmux ids never move. They are sorted NUMERICALLY, not
    lexicographically: after ``$0``..``$8`` are killed, a string sort
    calls ``$10`` the lowest of ``$9``, ``$10``, ``$11`` -- wrong, and
    only past nine, which is exactly where it would go unnoticed
    longest.
    """
    return min(objects, key=lambda obj: tmux_id_sort_key(getattr(obj, id_field, None)))


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
            _raise_if_server_unreachable(server)
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
            _raise_if_server_unreachable(server)
            raise exc.TmuxObjectDoesNotExist(
                obj_key="session_name",
                obj_id=session_name,
                list_cmd="list-sessions",
                list_extra_args=(),
            )
        return session

    sessions = server.sessions
    if not sessions:
        _raise_if_server_unreachable(server)
        raise exc.TmuxObjectDoesNotExist(
            obj_key="session",
            obj_id="(any)",
            list_cmd="list-sessions",
            list_extra_args=(),
        )
    return t.cast("Session", _oldest(list(sessions), "session_id"))


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
    return t.cast("Window", _oldest(list(windows), "window_id"))


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
    return t.cast("Pane", _oldest(list(panes), "pane_id"))


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
#: collection tests, and libtmux's lookups fall through to ``False`` for
#: a bool -- so allowing them answers every query with an empty list,
#: contradictory pairs like ``__in``/``__nin`` included.
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


def _as_tmux_text(value: str | bool | int) -> str | bool | int:
    """Render a typed filter value the way tmux reports the field.

    tmux-derived attributes are always STRINGS -- ``pane_width`` is
    ``"80"``, ``pane_active`` is ``"1"``. Comparing them against a real
    ``80`` or ``True`` matches nothing, so accepting typed values in the
    schema without this would trade a validation error for a confident
    empty result. Booleans first: ``bool`` is a subclass of ``int``.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return value


def _apply_filters(
    items: t.Any,
    filters: dict[str, str | bool | int] | str | None,
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
        # Matching QueryList: an unknown trailing segment is part of the
        # attribute path with the operator defaulting to ``exact``, so
        # ``active_pane__pane_id`` traverses.
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
            attr_filters[key] = _as_tmux_text(value)
        elif field in _MODEL_FIELD_ALIASES:
            attr_filters[_MODEL_FIELD_ALIASES[field] + key[len(field) :]] = (
                _as_tmux_text(value)
            )
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
    # ``getattr`` so a build without ``Session.active_pane``, or a session
    # mid-teardown with none, reads as ``None`` and ``list_sessions`` still
    # serialises.
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
        session_name=pane.session_name,
        is_caller=_compute_is_caller(pane),
    )


P = t.ParamSpec("P")
R = t.TypeVar("R")


#: tmux stderr fragments meaning the socket has no daemon behind it.
#: Anything else on a failed ``list-sessions`` -- a protocol mismatch, a
#: permission error -- is a server that exists and cannot be reached,
#: which is a different answer.
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

    There is a THIRD case stderr cannot report, because nothing is
    written: a server spinning inside its own event loop accepts the
    connection and never replies. ``Server.cmd`` has no timeout, so the
    probe meant to classify the server hung on it instead. Bounded here,
    and a timeout is reported as unreachable -- which is what it is.
    """
    try:
        result = _run_tmux_sync(
            server, "list-sessions", timeout=_LIVENESS_TIMEOUT_SECONDS
        )
    except Exception as err:  # noqa: BLE001 - probe must not raise
        return False, str(err)

    if result is None:
        return False, HUNG_SOCKET_REASON

    if result.returncode == 0:
        return True, None

    detail = result.stderr.strip()
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
