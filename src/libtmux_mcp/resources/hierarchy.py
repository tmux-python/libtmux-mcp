"""MCP resources for tmux object hierarchy."""

from __future__ import annotations

import json
import typing as t

from fastmcp.exceptions import ResourceError
from fastmcp.resources import ResourceSecurity
from libtmux import exc as libtmux_exc
from mcp.types import ResourceTemplateReference

from libtmux_mcp._utils import (
    _get_server,
    _serialize_pane,
    _serialize_session,
    _serialize_window,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

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

#: Path screening for the ``tmux://`` templates.
#:
#: FastMCP screens extracted template parameters before a handler runs.
#: Its absolute-path check reads two legitimate tmux identifiers as
#: filesystem paths: the socket paths ``list_servers`` reports, and any
#: ``<letter>:<rest>`` session name, which tmux accepts. Both must reach
#: the handler so ``tmux://`` and the equivalent tools agree on which
#: sessions and servers exist.
#:
#: Traversal and null-byte screening stay on: ``socket_name`` reaches
#: ``tmux -L``, which appends it to the socket directory without
#: normalising, so ``../..`` really does place the socket elsewhere.
#: Set per template rather than server-wide so a future resource that
#: does join a parameter onto a path keeps the secure default.
_TMUX_PATH_SECURITY = ResourceSecurity(reject_absolute_paths=False)


def register_completions(mcp: FastMCP) -> None:
    """Answer argument completion for the ``tmux://`` templates.

    The templates take live tmux identifiers, which a reader cannot guess
    and the template listing does not carry — MCP publishes only the URI
    template itself, never its parameter domain. A completion handler is
    the protocol's way to offer them.

    Agent clients do not send ``completion/complete``; human-facing hosts
    (the MCP Inspector's UI, VS Code) do, which is who this serves.
    """

    @mcp.completion
    def complete_tmux_argument(
        ref: t.Any,
        argument: t.Any,
        context: t.Any,
    ) -> list[str] | None:
        """Return live tmux identifiers matching what has been typed."""
        if not isinstance(ref, ResourceTemplateReference):
            return None
        supplied = (getattr(context, "arguments", None) or {}) if context else {}
        try:
            server = _get_server(socket_name=supplied.get("socket_name"))
            if argument.name == "session_name":
                pool = [s.session_name or "" for s in server.sessions]
            elif argument.name == "pane_id":
                pool = [p.pane_id or "" for p in server.panes]
            elif argument.name == "window_index":
                pool = [w.window_index or "" for w in server.windows]
            else:
                return None
        except (libtmux_exc.LibTmuxException, OSError):
            # A completion is a convenience; a dead or missing tmux server
            # must not turn it into a failed request.
            return None
        prefix = argument.value or ""
        return [value for value in pool if value.startswith(prefix)]


def register(mcp: FastMCP) -> None:
    """Register hierarchy resources with the FastMCP instance."""

    @mcp.resource(
        "tmux://sessions{?socket_name}",
        title="All Sessions",
        mime_type=_JSON_MIME,
        security=_TMUX_PATH_SECURITY,
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
        sessions = [_serialize_session(s).model_dump() for s in server.sessions]
        return json.dumps(sessions, indent=2)

    @mcp.resource(
        "tmux://sessions/{session_name}{?socket_name}",
        title="Session Detail",
        mime_type=_JSON_MIME,
        security=_TMUX_PATH_SECURITY,
    )
    def get_session(
        session_name: str,
        socket_name: str | None = None,
    ) -> str:
        """Get details of a specific tmux session.

        Parameters
        ----------
        session_name : str
            The session name.
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            JSON object with session info and its windows
            (MIME: ``application/json``).
        """
        server = _get_server(socket_name=socket_name)
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
        security=_TMUX_PATH_SECURITY,
    )
    def get_session_windows(
        session_name: str,
        socket_name: str | None = None,
    ) -> str:
        """List all windows in a tmux session.

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
        security=_TMUX_PATH_SECURITY,
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
        security=_TMUX_PATH_SECURITY,
    )
    def get_pane(pane_id: str, socket_name: str | None = None) -> str:
        """Get details of a specific pane.

        Parameters
        ----------
        pane_id : str
            The pane ID (e.g. '%1').
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            JSON object of pane details (MIME: ``application/json``).
        """
        server = _get_server(socket_name=socket_name)
        pane = server.panes.get(pane_id=pane_id, default=None)
        if pane is None:
            msg = f"Pane not found: {pane_id}"
            raise ResourceError(msg)

        return json.dumps(_serialize_pane(pane).model_dump(), indent=2)

    @mcp.resource(
        "tmux://panes/{pane_id}/content{?socket_name}",
        title="Pane Content",
        mime_type=_TEXT_MIME,
        security=_TMUX_PATH_SECURITY,
    )
    def get_pane_content(pane_id: str, socket_name: str | None = None) -> str:
        """Capture and return the content of a pane.

        Parameters
        ----------
        pane_id : str
            The pane ID (e.g. '%1').
        socket_name : str, optional
            tmux socket name. Defaults to LIBTMUX_SOCKET env var.

        Returns
        -------
        str
            Plain text captured pane content (MIME: ``text/plain``).
        """
        server = _get_server(socket_name=socket_name)
        pane = server.panes.get(pane_id=pane_id, default=None)
        if pane is None:
            msg = f"Pane not found: {pane_id}"
            raise ResourceError(msg)

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
