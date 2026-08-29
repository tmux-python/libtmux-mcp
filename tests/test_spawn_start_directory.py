"""Tests for ``start_directory`` handling across the tmux spawn tools."""

from __future__ import annotations

import pathlib
import typing as t

import pytest

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.tools.pane_tools import respawn_pane
from libtmux_mcp.tools.server_tools import create_session
from libtmux_mcp.tools.session_tools import create_window
from libtmux_mcp.tools.window_tools import split_window

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session

#: Spawn tools keyed by name, each called with only ``start_directory``
#: and the target it needs, and each returning the resulting pane ID.
_SPAWNERS: dict[str, t.Callable[[Server, Session, Pane, str], str]] = {
    "create_session": lambda server, session, pane, cwd: t.cast(
        "str",
        create_session(
            session_name="sd-probe",
            start_directory=cwd,
            socket_name=server.socket_name,
        ).active_pane_id,
    ),
    "create_window": lambda server, session, pane, cwd: t.cast(
        "str",
        create_window(
            session_id=session.session_id,
            start_directory=cwd,
            socket_name=server.socket_name,
        ).active_pane_id,
    ),
    "split_window": lambda server, session, pane, cwd: (
        split_window(
            pane_id=pane.pane_id,
            start_directory=cwd,
            socket_name=server.socket_name,
        ).pane_id
    ),
    "respawn_pane": lambda server, session, pane, cwd: (
        respawn_pane(
            pane_id=t.cast("str", pane.pane_id),
            start_directory=cwd,
            socket_name=server.socket_name,
        ).pane_id
    ),
}


def _current_path(server: Server, pane_id: str) -> str:
    """Return a pane's working directory as tmux reports it."""
    pane = server.panes.get(pane_id=pane_id)
    assert pane is not None
    pane.refresh()
    return t.cast("str", pane.pane_current_path)


@pytest.mark.parametrize("spawner", list(_SPAWNERS))
def test_start_directory_does_not_run_tmux_format_jobs(
    mcp_server: Server,
    mcp_session: Session,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
    spawner: str,
) -> None:
    """A ``#(...)`` job in ``start_directory`` is refused, not executed."""
    marker = tmp_path / "executed"

    with pytest.raises(ExpectedToolError):
        _SPAWNERS[spawner](mcp_server, mcp_session, mcp_pane, f"#(touch {marker})")

    assert not marker.exists()


@pytest.mark.parametrize("spawner", list(_SPAWNERS))
@pytest.mark.parametrize(
    "name",
    ["plain", "has#hash", "job#(id)", "var#{x}", "style#[x]", "run##[x]"],
)
def test_start_directory_uses_the_directory_named(
    mcp_server: Server,
    mcp_session: Session,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
    spawner: str,
    name: str,
) -> None:
    """A directory whose real name contains ``#`` is used verbatim."""
    target = tmp_path / name
    target.mkdir()

    pane_id = _SPAWNERS[spawner](mcp_server, mcp_session, mcp_pane, str(target))

    assert _current_path(mcp_server, pane_id) == str(target)


def test_start_directory_rejects_a_missing_directory(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
) -> None:
    """A path that does not exist fails instead of falling back to ``$HOME``."""
    with pytest.raises(ExpectedToolError, match="not an existing directory"):
        split_window(
            pane_id=mcp_pane.pane_id,
            start_directory=str(tmp_path / "absent"),
            socket_name=mcp_server.socket_name,
        )
