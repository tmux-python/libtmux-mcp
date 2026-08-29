"""Reaching the tmux binary: argv, wall-clock bounds, and exec failures.

libtmux's `tmux_cmd` calls `Popen.communicate()` with no timeout, so a
wedged server blocks its caller forever. `_BoundedTmuxCmd` replaces it
process-wide with the same interface under a bound.
"""

from __future__ import annotations

import errno
import importlib
import logging
import shlex
import shutil
import subprocess
import typing as t

from libtmux import exc
from libtmux.common import tmux_cmd
from libtmux.server import Server

from libtmux_mcp._errors import ExpectedToolError

logger = logging.getLogger(__name__)


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


#: How long any single tmux call may take before this server calls the
#: tmux server unresponsive. Generous: a responsive tmux answers in
#: single-digit milliseconds, and capturing a 200,000-line history --
#: the slowest operation constructible here -- measured 37ms.
#:
#: THE one definition of that policy. Every other bound derives from it
#: rather than repeating the literal, because a second copy does not
#: fail when this one moves; the tools just start disagreeing.
_LIVENESS_TIMEOUT_SECONDS = 5.0


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
