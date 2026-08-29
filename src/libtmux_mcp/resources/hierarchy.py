"""MCP resources for tmux object hierarchy."""

from __future__ import annotations

import json
import typing as t

from fastmcp.exceptions import ResourceError

from libtmux_mcp._utils import (
    _get_server,
    _probe_liveness,
    _serialize_pane,
    _serialize_session,
    _serialize_window,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from libtmux.server import Server

#: MIME type advertised for resources that return structured tmux
#: metadata (session / window / pane views). Previously these resources
#: returned ``json.dumps(...)`` with no MIME annotation, so clients
#: treated the payload as opaque text. Declaring ``application/json``
#: lets clients parse it automatically.
_JSON_MIME = "application/json"

#: MIME type for the pane-content resource, which returns raw captured
#: terminal output that may contain ANSI control sequences. ``text/plain``
#: is the right choice — the consumer should neither JSON-parse it nor
#: render it as HTML.
_TEXT_MIME = "text/plain"


def _raise_if_unreachable(server: Server) -> None:
    """Refuse to answer for a server that exists but will not talk.

    ``server.sessions`` swallows the failure and yields an empty list,
    so a live session-holding server whose socket cannot be queried --
    a client/server protocol mismatch is the realistic cause -- reads
    as "no sessions". That is the wrong conclusion rather than a
    missing one.

    The tool path already discriminates this; the fix never reached
    here, so the two surfaces disagreed about the same server.
    """
    alive, reason = _probe_liveness(server)
    if not alive and reason is not None:
        msg = (
            f"tmux server exists but could not be queried: {reason}. "
            "Reporting no sessions here would be wrong rather than empty."
        )
        raise ResourceError(msg)


def _normalize_pane_id(pane_id: str) -> str:
    """Accept ``10`` as well as ``%10`` in a resource URI.

    The URI layer percent-decodes every captured template parameter
    (fastmcp ``resources/template.py``), and a tmux pane id starts with
    ``%``. ``%10`` therefore arrives as the single byte ``0x10``.
    ``%0``-``%9`` survive only because one trailing hex digit is an
    INVALID escape and passes through, so the whole surface works right
    up until a server has created its eleventh pane.

    Re-encoding the decoded byte is not a fix: ``%80``-``%99`` decode to
    bytes that are not valid UTF-8 and arrive as U+FFFD, so the digits
    are already gone. That repair passes every test written against low
    pane numbers and fails once a pane id reaches 128.

    A bare number needs no escaping at any pane number, so that is the
    spelling this accepts. ``%2510`` (a caller who pre-encoded the
    percent) still decodes to ``%10`` and keeps working.
    """
    if pane_id and not pane_id.startswith("%"):
        return f"%{pane_id}"
    return pane_id


def _raise_pane_not_found(pane_id: str) -> t.NoReturn:
    """Report a missing pane, naming a mangled id rather than hiding it."""
    msg = f"Pane not found: {pane_id!r}"
    if not pane_id.isprintable():
        msg += (
            " -- the id was percent-decoded by the URI layer. Address the "
            "pane by its bare number instead, e.g. 'tmux://panes/10'."
        )
    raise ResourceError(msg)


def register(mcp: FastMCP) -> None:
    """Register hierarchy resources with the FastMCP instance."""

    @mcp.resource(
        "tmux://sessions{?socket_name}",
        title="All Sessions",
        mime_type=_JSON_MIME,
    )
    def get_sessions(socket_name: str | None = None) -> str:
        """List all tmux sessions.

        Parameters
        ----------
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            JSON array of session objects (MIME: ``application/json``).
        """
        server = _get_server(socket_name=socket_name)
        _raise_if_unreachable(server)
        sessions = [_serialize_session(s).model_dump() for s in server.sessions]
        return json.dumps(sessions, indent=2)

    @mcp.resource(
        "tmux://sessions/{session_name}{?socket_name}",
        title="Session Detail",
        mime_type=_JSON_MIME,
    )
    def get_session(
        session_name: str,
        socket_name: str | None = None,
    ) -> str:
        """Get details of a specific tmux session.

        .. warning::

           Percent-encode the name when building this URI. The path
           segment is percent-DECODED before lookup, so a session
           literally named ``pct%20name`` is reached only as
           ``tmux://sessions/pct%2520name`` — pasting the raw name
           reads the session named ``pct name`` instead, silently and
           with no error. A space needs no encoding, which is what
           makes a literal ``%`` easy to miss.

           A ``/`` in the name needs the same treatment and fails
           differently: tmux permits a session named ``a/b``, and
           ``tmux://sessions/a/b`` does not match this template at all,
           so it is rejected as "Unknown resource" -- which reads as
           "no such endpoint" rather than "encode the name".
           ``tmux://sessions/a%2Fb`` reaches it.

        Parameters
        ----------
        session_name : str
            The session name, percent-encoded.
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            JSON object with session info and its windows
            (MIME: ``application/json``).
        """
        server = _get_server(socket_name=socket_name)
        _raise_if_unreachable(server)
        session = server.sessions.get(session_name=session_name, default=None)
        if session is None:
            msg = f"Session not found: {session_name}"
            raise ResourceError(msg)

        result: dict[str, t.Any] = _serialize_session(session).model_dump()
        result["windows"] = [_serialize_window(w).model_dump() for w in session.windows]
        return json.dumps(result, indent=2)

    @mcp.resource(
        "tmux://sessions/{session_name}/windows{?socket_name}",
        title="Session Windows",
        mime_type=_JSON_MIME,
    )
    def get_session_windows(
        session_name: str,
        socket_name: str | None = None,
    ) -> str:
        """List all windows in a tmux session.

        The session name is percent-decoded before lookup; see
        :func:`get_session` for why a name containing ``%`` must be
        encoded before it goes into this URI.

        Parameters
        ----------
        session_name : str
            The session name.
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            JSON array of window objects (MIME: ``application/json``).
        """
        server = _get_server(socket_name=socket_name)
        _raise_if_unreachable(server)
        session = server.sessions.get(session_name=session_name, default=None)
        if session is None:
            msg = f"Session not found: {session_name}"
            raise ResourceError(msg)

        windows = [_serialize_window(w).model_dump() for w in session.windows]
        return json.dumps(windows, indent=2)

    @mcp.resource(
        "tmux://sessions/{session_name}/windows/{window_index}{?socket_name}",
        title="Window Detail",
        mime_type=_JSON_MIME,
    )
    def get_window(
        session_name: str,
        window_index: str,
        socket_name: str | None = None,
    ) -> str:
        """Get details of a specific window in a session.

        Parameters
        ----------
        session_name : str
            The session name.
        window_index : str
            The window index within the session.
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            JSON object with window info and its panes
            (MIME: ``application/json``).
        """
        server = _get_server(socket_name=socket_name)
        _raise_if_unreachable(server)
        session = server.sessions.get(session_name=session_name, default=None)
        if session is None:
            msg = f"Session not found: {session_name}"
            raise ResourceError(msg)

        window = session.windows.get(window_index=window_index, default=None)
        if window is None:
            msg = f"Window not found: index {window_index}"
            raise ResourceError(msg)

        result: dict[str, t.Any] = _serialize_window(window).model_dump()
        result["panes"] = [_serialize_pane(p).model_dump() for p in window.panes]
        return json.dumps(result, indent=2)

    @mcp.resource(
        "tmux://panes/{pane_id}{?socket_name}",
        title="Pane Detail",
        mime_type=_JSON_MIME,
    )
    def get_pane(pane_id: str, socket_name: str | None = None) -> str:
        """Get details of a specific pane.

        Parameters
        ----------
        pane_id : str
            Pane number or id. Prefer the bare number ('10'):
            a URI-escaped '%10' is decoded to a control character
            before it reaches here.
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            JSON object of pane details (MIME: ``application/json``).
        """
        server = _get_server(socket_name=socket_name)
        _raise_if_unreachable(server)
        pane_id = _normalize_pane_id(pane_id)
        pane = server.panes.get(pane_id=pane_id, default=None)
        if pane is None:
            _raise_pane_not_found(pane_id)

        return json.dumps(_serialize_pane(pane).model_dump(), indent=2)

    @mcp.resource(
        "tmux://panes/{pane_id}/content{?socket_name}",
        title="Pane Content",
        mime_type=_TEXT_MIME,
    )
    def get_pane_content(pane_id: str, socket_name: str | None = None) -> str:
        """Capture and return the content of a pane.

        Parameters
        ----------
        pane_id : str
            Pane number or id. Prefer the bare number ('10'):
            a URI-escaped '%10' is decoded to a control character
            before it reaches here.
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            Plain text captured pane content (MIME: ``text/plain``).
        """
        server = _get_server(socket_name=socket_name)
        _raise_if_unreachable(server)
        pane_id = _normalize_pane_id(pane_id)
        pane = server.panes.get(pane_id=pane_id, default=None)
        if pane is None:
            _raise_pane_not_found(pane_id)

        lines = pane.capture_pane()
        return "\n".join(lines)

    # Type checkers: list the functions to silence unused-name warnings
    # without exposing them outside this closure.
    _ = (
        get_sessions,
        get_session,
        get_session_windows,
        get_window,
        get_pane,
        get_pane_content,
    )


__all__ = ["register"]
