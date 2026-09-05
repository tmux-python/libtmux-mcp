"""Integration tests for libtmux MCP resources via FastMCP Client."""

from __future__ import annotations

import asyncio
import json
import typing as t

import pytest
from fastmcp import Client
from mcp.shared.exceptions import MCPError

from libtmux_mcp._utils import _server_cache
from libtmux_mcp.server import _register_all, mcp
from libtmux_mcp.tools.session_tools import get_session_info

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session
    from libtmux.window import Window


_registered = False


@pytest.fixture(autouse=True)
def _ensure_registered() -> None:
    """Ensure tools and resources are registered with the MCP server once."""
    global _registered
    if not _registered:
        _register_all()
        _registered = True


def _run(coro: t.Any) -> t.Any:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


async def _read(uri: str) -> str:
    """Read a resource via FastMCP Client and return text content."""
    async with Client(mcp) as client:
        results = await client.read_resource(uri)
        assert len(results) >= 1
        return results[0].text or ""


class ResourceIntegrationFixture(t.NamedTuple):
    """Test fixture for resource integration reads."""

    test_id: str
    uri_template: str
    expect_json: bool
    expect_contains: str | None


RESOURCE_INTEGRATION_FIXTURES: list[ResourceIntegrationFixture] = [
    ResourceIntegrationFixture(
        test_id="list_all_sessions",
        uri_template="tmux://sessions",
        expect_json=True,
        expect_contains="session_id",
    ),
    ResourceIntegrationFixture(
        test_id="session_detail",
        uri_template="tmux://sessions/{session_name}",
        expect_json=True,
        expect_contains="windows",
    ),
    ResourceIntegrationFixture(
        test_id="session_windows",
        uri_template="tmux://sessions/{session_name}/windows",
        expect_json=True,
        expect_contains="window_id",
    ),
    ResourceIntegrationFixture(
        test_id="pane_detail",
        uri_template="tmux://panes/{pane_id}",
        expect_json=True,
        expect_contains="pane_id",
    ),
    ResourceIntegrationFixture(
        test_id="pane_content",
        uri_template="tmux://panes/{pane_id}/content",
        expect_json=False,
        expect_contains=None,  # fresh pane may be empty
    ),
]


@pytest.mark.parametrize(
    ResourceIntegrationFixture._fields,
    RESOURCE_INTEGRATION_FIXTURES,
    ids=[f.test_id for f in RESOURCE_INTEGRATION_FIXTURES],
)
def test_resource_read_via_client(
    mcp_server: Server,
    mcp_session: Session,
    mcp_window: Window,
    mcp_pane: Pane,
    test_id: str,
    uri_template: str,
    expect_json: bool,
    expect_contains: str | None,
) -> None:
    """Resources are readable via FastMCP Client protocol."""
    uri = uri_template.format(
        session_name=mcp_session.session_name,
        window_index=mcp_window.window_index,
        pane_id=mcp_pane.pane_id,
    )

    text = _run(_read(uri))
    assert isinstance(text, str)

    if expect_json:
        data = json.loads(text)
        assert data is not None

    if expect_contains is not None:
        assert expect_contains in text


# ---------------------------------------------------------------------------
# Path-like parameter values.
#
# Every ``tmux://`` template carries ``{?socket_name}``, and two of its
# parameters routinely hold values a generic screen reads as filesystem
# paths: a socket path is absolute, and tmux accepts session names such
# as ``a:1`` that look drive-relative. Both must reach the handler, so
# ``tmux://`` and the equivalent tools agree on what exists — while a
# ``..`` component, which really does escape tmux's socket directory,
# must not.
#
# Sessions are created and renamed with raw tmux: libtmux's own
# ``new_session`` and ``rename_session`` reject colons and periods, so
# going through them would hide exactly what these guard.
# ---------------------------------------------------------------------------

#: A session name that is legal in tmux and path-shaped to a screen.
PATH_LIKE_SESSION_NAME = "a:1"


def _socket_path(server: Server) -> str:
    """Return the absolute socket path a server is listening on.

    This is the spelling ``list_servers`` reports, so it is the value a
    client is most likely to send back.
    """
    return server.cmd("display-message", "-p", "#{socket_path}").stdout[0]


class PathLikeResourceFixture(t.NamedTuple):
    """A ``tmux://`` template read with path-like parameter values.

    Attributes
    ----------
    test_id : str
        Identifier shown in the parametrized test name.
    uri_template : str
        ``tmux://`` URI with ``{}`` placeholders for the tmux targets.
    """

    test_id: str
    uri_template: str


PATH_LIKE_RESOURCE_FIXTURES: list[PathLikeResourceFixture] = [
    PathLikeResourceFixture(
        "all_sessions",
        "tmux://sessions?socket_name={socket_path}",
    ),
    PathLikeResourceFixture(
        "session_detail",
        "tmux://sessions/{session_name}?socket_name={socket_path}",
    ),
    PathLikeResourceFixture(
        "session_windows",
        "tmux://sessions/{session_name}/windows?socket_name={socket_path}",
    ),
    PathLikeResourceFixture(
        "window_detail",
        "tmux://sessions/{session_name}/windows/{window_index}"
        "?socket_name={socket_path}",
    ),
    PathLikeResourceFixture(
        "pane_detail",
        "tmux://panes/{pane_id}?socket_name={socket_path}",
    ),
    PathLikeResourceFixture(
        "pane_content",
        "tmux://panes/{pane_id}/content?socket_name={socket_path}",
    ),
]


@pytest.mark.parametrize(
    PathLikeResourceFixture._fields,
    PATH_LIKE_RESOURCE_FIXTURES,
    ids=[f.test_id for f in PATH_LIKE_RESOURCE_FIXTURES],
)
def test_resource_read_accepts_path_like_parameters(
    mcp_server: Server,
    mcp_session: Session,
    mcp_window: Window,
    mcp_pane: Pane,
    test_id: str,
    uri_template: str,
) -> None:
    """Every template stays readable with an absolute socket and ``a:1``."""
    assert test_id
    mcp_server.cmd(
        "rename-session",
        "-t",
        mcp_session.session_id,
        PATH_LIKE_SESSION_NAME,
    )
    socket_path = _socket_path(mcp_server)
    _server_cache[(socket_path, None, None)] = mcp_server

    uri = uri_template.format(
        session_name=PATH_LIKE_SESSION_NAME,
        window_index=mcp_window.window_index,
        pane_id=mcp_pane.pane_id,
        socket_path=socket_path,
    )

    assert isinstance(_run(_read(uri)), str)


@pytest.mark.parametrize("session_name", ["a:1", "x:", "a..b"])
def test_resource_and_tool_agree_on_path_like_session_names(
    mcp_server: Server,
    session_name: str,
) -> None:
    """A session readable through a tool is readable through ``tmux://``."""
    mcp_server.cmd("new-session", "-d", "-s", session_name)

    payload = json.loads(_run(_read(f"tmux://sessions/{session_name}")))
    info = get_session_info(
        session_name=session_name,
        socket_name=mcp_server.socket_name,
    )

    assert payload["session_name"] == session_name == info.session_name


def test_socket_name_traversal_never_reaches_tmux() -> None:
    """A ``..`` socket name is refused before a server is built for it.

    tmux appends a ``-L`` value to its socket directory without
    normalising it, so ``../..`` puts the socket somewhere else
    entirely. URI normalization does not help: it collapses ``..`` in
    the path, not in the query string.
    """
    socket_name = "../../escaped"

    with pytest.raises(MCPError):
        _run(_read(f"tmux://sessions?socket_name={socket_name}"))

    assert (socket_name, None, None) not in _server_cache
