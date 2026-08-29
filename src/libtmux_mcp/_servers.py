"""The process-wide server cache, and the liveness it is gated on.

A handle is probed before it is handed out and re-probed on every
cache hit: nothing downstream of `Server` is bounded. Every read and
write of the cache runs under `_server_cache_lock`.
"""

from __future__ import annotations

import logging
import os
import threading
import typing as t

from libtmux.server import Server

from libtmux_mcp._errors import ExpectedToolError
from libtmux_mcp._exec import (
    _LIVENESS_TIMEOUT_SECONDS,
    _install_bounded_tmux_cmd,
    _run_tmux_sync,
    _tmux_argv,
)
from libtmux_mcp._tmux_proc import _run_tmux_bounded as _run_tmux_async

logger = logging.getLogger(__name__)


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


#: Distinguishable by identity, so callers can tell "did not answer"
#: from every other unreachable reason without matching on prose.
HUNG_SOCKET_REASON = (
    "the tmux server accepted the connection but did not answer within "
    f"{_LIVENESS_TIMEOUT_SECONDS:g}s"
)


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


def _drain_server_cache() -> list[Server]:
    """Empty the cache and return the servers it held.

    The caller works on a snapshot, so per-server teardown work never
    runs with an iterator open on a dict another thread may fill.
    """
    with _server_cache_lock:
        servers = list(_server_cache.values())
        _server_cache.clear()
        return servers


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
