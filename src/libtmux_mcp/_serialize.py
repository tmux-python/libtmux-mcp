"""libtmux objects to the pydantic models tools return.

Each record is built from one tmux round trip, so its fields describe a
single moment rather than a sequence of them.
"""

from __future__ import annotations

import logging
import typing as t

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.session import Session
    from libtmux.window import Window

    from libtmux_mcp.models import PaneInfo, SessionInfo, WindowInfo

from libtmux_mcp._caller import _compute_is_caller

logger = logging.getLogger(__name__)


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
