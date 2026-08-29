"""Tools that take a literal must store the literal, not its expansion.

tmux expands ``#{...}`` in the name/title arguments of ``rename-window``,
``rename-session``, ``select-pane -T``, ``new-window -n`` and
``new-session``, and in the option *name* of ``set-option`` /
``show-options``. None of those expansions can be turned off with a
flag, so the server escapes on the way in. See
:mod:`libtmux_mcp._tmux_format`.
"""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp._tmux_format import (
    contains_format_job,
    escape_format,
    escape_format_time,
)
from libtmux_mcp.tools.option_tools import set_option, show_option
from libtmux_mcp.tools.pane_tools.lifecycle import set_pane_title
from libtmux_mcp.tools.pane_tools.meta import display_message
from libtmux_mcp.tools.server_tools import create_session
from libtmux_mcp.tools.session_tools import create_window, rename_session
from libtmux_mcp.tools.window_tools import rename_window

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session
    from libtmux.window import Window

#: Values that tmux's format expander would rewrite. Each is a real
#: rewrite measured against tmux e802909d, not a hypothetical: ``#S``
#: is the session name, ``#(...)`` runs a command job and is substituted
#: away entirely, and ``#[...]`` is the one form that must NOT be
#: escaped -- tmux copies a ``#``-run followed by ``[`` verbatim and
#: never collapses ``##[``, so doubling it corrupts the value.
HOSTILE = [
    pytest.param("#{pane_id}", id="format-substitution"),
    pytest.param("#S", id="legacy-alias"),
    pytest.param("#(echo hi)", id="command-job"),
    pytest.param("#[fg=red]", id="style-sequence-left-alone"),
    pytest.param("##{pane_id}", id="already-doubled"),
    pytest.param("issue #42", id="bare-hash"),
    pytest.param("100%done", id="percent-is-literal-here"),
    pytest.param("plain", id="unaffected"),
]


@pytest.mark.parametrize("value", HOSTILE)
def test_escape_format_round_trips_through_tmux(server: Server, value: str) -> None:
    """``escape_format`` is the exact inverse of ``format_expand``."""
    session = server.new_session(session_name="esc")
    pane = session.active_window.active_pane
    assert pane is not None
    pane.cmd("select-pane", "-T", escape_format(value))
    assert pane.cmd("display-message", "-p", "#{pane_title}").stdout[0] == value


def test_escape_format_leaves_percent_alone() -> None:
    """Only ``pipe-pane`` is on tmux's strftime path; nothing else is."""
    assert escape_format("date-%Y.log") == "date-%Y.log"
    assert escape_format_time("date-%Y.log") == "date-%%Y.log"


@pytest.mark.parametrize("value", HOSTILE)
def test_set_pane_title_stores_the_literal(
    mcp_server: Server, mcp_session: Session, value: str
) -> None:
    """set_pane_title(X) then reading the title back yields X."""
    pane = mcp_session.active_window.active_pane
    assert pane is not None
    result = set_pane_title(
        title=value, pane_id=pane.pane_id, socket_name=mcp_server.socket_name
    )
    assert result.pane_title == value


@pytest.mark.parametrize("value", HOSTILE)
def test_rename_window_stores_the_literal(
    mcp_server: Server, mcp_window: Window, value: str
) -> None:
    """rename_window(X) names the window X."""
    result = rename_window(
        new_name=value,
        window_id=mcp_window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_name == value


@pytest.mark.parametrize("value", HOSTILE)
def test_create_window_stores_the_literal(
    mcp_server: Server, mcp_session: Session, value: str
) -> None:
    """create_window(window_name=X) names the window X."""
    result = create_window(
        window_name=value,
        session_id=mcp_session.session_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.window_name == value


@pytest.mark.parametrize("value", HOSTILE)
def test_rename_session_stores_the_literal(
    mcp_server: Server, mcp_session: Session, value: str
) -> None:
    """rename_session(X) names the session X."""
    result = rename_session(
        new_name=value,
        session_id=mcp_session.session_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.session_name == value


@pytest.mark.parametrize("value", HOSTILE)
def test_create_session_stores_the_literal(mcp_server: Server, value: str) -> None:
    """create_session(session_name=X, window_name=X) uses X for both.

    ``SessionInfo`` carries no window list, so the window half is read
    off the server -- the result alone cannot confirm it.
    """
    result = create_session(
        session_name=value,
        window_name=value,
        socket_name=mcp_server.socket_name,
    )
    assert result.session_name == value
    session = mcp_server.sessions.get(session_id=result.session_id)
    assert session is not None
    assert session.windows[0].window_name == value


@pytest.mark.parametrize("tool", [set_option, show_option])
def test_option_name_with_a_format_sequence_is_refused(
    mcp_server: Server, mcp_session: Session, tool: t.Any
) -> None:
    """A rewritable option name is refused rather than silently redirected.

    tmux expands the option NAME, so ``@a#{pane_id}`` addresses ``@a%0``
    -- a different option depending on what the call resolved against.
    Escaping it does not help: libtmux looks the result up under the
    name the caller passed while tmux answers under the stored one, so
    an escaped name is writable and permanently unreadable.
    """
    kwargs: dict[str, t.Any] = {
        "option": "@qa#{pane_id}",
        "scope": "session",
        "target": mcp_session.session_name,
        "socket_name": mcp_server.socket_name,
    }
    if tool is set_option:
        kwargs["value"] = "sentinel"
    with pytest.raises(ToolError, match="tmux format sequence"):
        tool(**kwargs)


def test_ordinary_option_names_still_work(
    mcp_server: Server, mcp_session: Session
) -> None:
    """The guard must not catch a name without a format sequence."""
    set_option(
        option="@qa_plain",
        value="sentinel",
        scope="session",
        target=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    result = show_option(
        option="@qa_plain",
        scope="session",
        target=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert result.value == "sentinel"


def test_expansion_cannot_manufacture_a_name_the_validator_forbids(
    mcp_server: Server,
) -> None:
    """The name that reaches tmux is the name that was validated.

    Validation ran on the caller's literal while tmux acted on the
    expansion, so a name the validator rejects when typed could still
    be produced by expanding one it accepts:
    ``create_session(session_name="#{pane_current_path}")`` expanded to
    a path, and ``clean_name`` reduced that to the EMPTY name the
    validator exists to forbid. The session was then unreachable by
    name -- ``rename_session`` could not even rename it back.
    """
    value = "#{pane_current_path}"
    result = create_session(session_name=value, socket_name=mcp_server.socket_name)
    assert result.session_name == value
    session = mcp_server.sessions.get(session_id=result.session_id)
    assert session is not None
    assert session.session_name == value


@pytest.mark.parametrize(
    ("value", "is_job"),
    [
        pytest.param("#(x)", True, id="one-hash-is-a-job"),
        pytest.param("##(x)", False, id="two-hashes-are-a-literal"),
        pytest.param("###(x)", True, id="three-hashes-are-a-job-again"),
        pytest.param("####(x)", False, id="four-hashes-are-a-literal"),
        pytest.param("pane #{pane_id}", False, id="no-paren"),
        pytest.param("a ##(b) c #(d)", True, id="a-later-odd-run-still-counts"),
        pytest.param("#{?#(cmd),a,b}", True, id="nested-inside-a-conditional"),
        pytest.param("plain", False, id="unaffected"),
    ],
)
def test_contains_format_job_reads_the_run_parity(value: str, is_job: bool) -> None:
    """Only an ODD ``#``-run before ``(`` opens a job.

    ``format_expand1`` consumes ``#`` pairs into a literal ``#`` before
    it looks for ``(``, so an even run leaves nothing to start one.
    """
    assert contains_format_job(value) is is_job


def test_display_message_allows_an_escaped_literal_job(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``##(`` is text, and tmux renders it as text.

    The guard tested for the substring ``#(``, which also matched the
    escaped form -- so a label or a code snippet containing ``#(`` was
    refused even though nothing would run.
    """
    assert (
        display_message(
            format_string="pane ##(literal)",
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
        == "pane #(literal)"
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("#(echo pwned)", id="bare-job"),
        pytest.param("###(echo pwned)", id="odd-run-still-a-job"),
        pytest.param("x #(echo pwned) y", id="embedded"),
        pytest.param("#{?#(echo pwned),a,b}", id="inside-a-conditional"),
    ],
)
def test_display_message_still_refuses_a_real_job(
    mcp_server: Server, mcp_pane: Pane, payload: str
) -> None:
    """Relaxing the guard must not let a job through."""
    with pytest.raises(ToolError, match="format jobs"):
        display_message(
            format_string=payload,
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
