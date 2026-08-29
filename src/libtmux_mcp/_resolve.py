"""Turning a caller's id or name into a live libtmux object.

Resolution is by explicit id first, then by name, then by the oldest
candidate, so an untargeted call is deterministic rather than arbitrary.
"""

from __future__ import annotations

import logging
import typing as t

from libtmux import exc
from libtmux.server import Server

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.session import Session
    from libtmux.window import Window


from libtmux_mcp._servers import _raise_if_server_unreachable

logger = logging.getLogger(__name__)


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
