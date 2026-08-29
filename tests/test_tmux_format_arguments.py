"""Tests for caller text reaching a tmux argument that tmux expands.

tmux expands several argument values as formats, where ``#H`` becomes the
hostname and ``#(cmd)`` runs a shell job. Every such argument is covered
here, so a new one cannot be added without a matching row.
"""

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
    from libtmux.window import Window

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


#: Text exercising each way a tmux format rewrites an argument: a
#: single-character alias, a variable, and a command job.
FORMAT_BEARING_NAMES = ["plain", "host#Hname", "sess#Sname", "job#(id)"]


@pytest.mark.parametrize("text", FORMAT_BEARING_NAMES)
def test_rename_session_stores_the_name_given(
    mcp_server: Server,
    mcp_session: Session,
    text: str,
) -> None:
    """``tmux rename-session`` expands its argument; the stored name matches."""
    from libtmux_mcp.tools.session_tools import rename_session

    renamed = rename_session(
        new_name=text,
        session_id=mcp_session.session_id,
        socket_name=mcp_server.socket_name,
    )

    assert renamed.session_name == text


@pytest.mark.parametrize("text", FORMAT_BEARING_NAMES)
def test_rename_window_stores_the_name_given(
    mcp_server: Server,
    mcp_window: Window,
    text: str,
) -> None:
    """``tmux rename-window`` expands its argument; the stored name matches."""
    from libtmux_mcp.tools.window_tools import rename_window

    renamed = rename_window(
        new_name=text,
        window_id=mcp_window.window_id,
        socket_name=mcp_server.socket_name,
    )

    assert renamed.window_name == text


@pytest.mark.parametrize("text", FORMAT_BEARING_NAMES)
def test_set_pane_title_stores_the_title_given(
    mcp_server: Server,
    mcp_pane: Pane,
    text: str,
) -> None:
    """``tmux select-pane -T`` expands its argument; the stored title matches."""
    from libtmux_mcp.tools.pane_tools import set_pane_title

    set_pane_title(
        title=text,
        pane_id=t.cast("str", mcp_pane.pane_id),
        socket_name=mcp_server.socket_name,
    )

    mcp_pane.refresh()
    assert mcp_pane.pane_title == text


def test_set_option_stores_the_option_named(
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """``set-option`` and ``show-options`` both expand the option name."""
    from libtmux_mcp.tools.option_tools import set_option

    name = "@probe#Hopt"
    set_option(
        option=name,
        value="1",
        global_=True,
        socket_name=mcp_server.socket_name,
    )

    stored = mcp_server.cmd("show-options", "-g").stdout
    assert any(line.startswith(f"{name} ") for line in stored), stored


@pytest.mark.parametrize("text", FORMAT_BEARING_NAMES)
def test_create_session_stores_the_names_given(
    mcp_server: Server,
    text: str,
) -> None:
    """``new-session`` expands both ``-s`` and ``-n``."""
    created = create_session(
        session_name=text,
        window_name=text,
        socket_name=mcp_server.socket_name,
    )

    assert created.session_name == text
    session = mcp_server.sessions.get(session_id=created.session_id)
    assert session is not None
    assert session.active_window.window_name == text


@pytest.mark.parametrize("text", FORMAT_BEARING_NAMES)
def test_create_window_stores_the_name_given(
    mcp_server: Server,
    mcp_session: Session,
    text: str,
) -> None:
    """``new-window`` expands ``-n``."""
    created = create_window(
        session_id=mcp_session.session_id,
        window_name=text,
        socket_name=mcp_server.socket_name,
    )

    assert created.window_name == text


@pytest.mark.parametrize(
    "format_string",
    [
        "#(touch /tmp/evil)",
        "#{E:@opt}",
        "#{E:pane_current_path}",
        "#{T:status-left}",
        "#S",
        "##",
    ],
)
def test_display_message_accepts_only_variable_references(
    mcp_server: Server,
    mcp_pane: Pane,
    format_string: str,
) -> None:
    """Anything but ``#{name}`` is refused, including a second expansion.

    ``#{E:...}`` re-expands a variable's *value*, and a pane's own working
    directory reaches ``pane_current_path`` unsanitized, so a blocklist on
    the caller's text cannot see what would run.
    """
    from libtmux_mcp.tools.pane_tools import display_message

    with pytest.raises(ExpectedToolError):
        display_message(
            format_string=format_string,
            pane_id=t.cast("str", mcp_pane.pane_id),
            socket_name=mcp_server.socket_name,
        )


def test_display_message_expands_plain_variables(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """A format of bare ``#{name}`` references still works."""
    from libtmux_mcp.tools.pane_tools import display_message

    result = display_message(
        format_string="id=#{pane_id} zoomed=#{window_zoomed_flag}",
        pane_id=t.cast("str", mcp_pane.pane_id),
        socket_name=mcp_server.socket_name,
    )

    assert result == f"id={mcp_pane.pane_id} zoomed=0"


def test_the_hash_escaper_owns_only_the_hash_expander() -> None:
    """Escaping is per interpreter, and applying it twice is not a no-op.

    ``pipe_pane`` adds ``%`` doubling because ``pipe-pane`` runs its
    argument through ``strftime`` as well; ``-c`` and the name arguments
    do not, so a percent there is literal. Whoever owns a value escapes
    it once, at the boundary that knows which expanders it will meet.
    """
    from libtmux_mcp._utils import _escape_tmux_format

    assert _escape_tmux_format("100%done") == "100%done"
    assert _escape_tmux_format("log-%Y.txt") == "log-%Y.txt"
    assert _escape_tmux_format("a#b") == "a##b"
    assert _escape_tmux_format("a##b") == "a####b"
    assert _escape_tmux_format(_escape_tmux_format("a#b")) == "a####b"
    assert _escape_tmux_format("") == ""
