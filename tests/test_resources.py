"""Tests for libtmux MCP resources."""

from __future__ import annotations

import json
import typing as t

import pytest

from libtmux_mcp.resources.hierarchy import register

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session
    from libtmux.window import Window


@pytest.fixture
def resource_functions(mcp_server: Server) -> dict[str, t.Any]:
    """Register resources and return the function references.

    Since resources are registered via decorators, we capture them
    by creating a mock FastMCP and collecting registered functions.
    """
    functions: dict[str, t.Any] = {}

    class MockMCP:
        def resource(self, uri: str, **kwargs: t.Any) -> t.Any:
            def decorator(fn: t.Any) -> t.Any:
                functions[uri] = fn
                return fn

            return decorator

    register(MockMCP())  # type: ignore[arg-type]
    return functions


def test_sessions_resource(
    resource_functions: dict[str, t.Any], mcp_session: Session
) -> None:
    """tmux://sessions returns session list."""
    fn = resource_functions["tmux://sessions{?socket_name}"]
    result = fn()
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) >= 1


def test_session_detail_resource(
    resource_functions: dict[str, t.Any], mcp_session: Session
) -> None:
    """tmux://sessions/{name} returns session with windows."""
    fn = resource_functions["tmux://sessions/{session_name}{?socket_name}"]
    result = fn(mcp_session.session_name)
    data = json.loads(result)
    assert "session_id" in data
    assert "windows" in data


def test_session_windows_resource(
    resource_functions: dict[str, t.Any], mcp_session: Session
) -> None:
    """tmux://sessions/{name}/windows returns window list."""
    fn = resource_functions["tmux://sessions/{session_name}/windows{?socket_name}"]
    result = fn(mcp_session.session_name)
    data = json.loads(result)
    assert isinstance(data, list)


def test_window_detail_resource(
    resource_functions: dict[str, t.Any],
    mcp_session: Session,
    mcp_window: Window,
) -> None:
    """tmux://sessions/{name}/windows/{index} returns window with panes."""
    fn = resource_functions[
        "tmux://sessions/{session_name}/windows/{window_index}{?socket_name}"
    ]
    result = fn(mcp_session.session_name, mcp_window.window_index)
    data = json.loads(result)
    assert "window_id" in data
    assert "panes" in data


def test_pane_detail_resource(
    resource_functions: dict[str, t.Any], mcp_pane: Pane
) -> None:
    """tmux://panes/{pane_id} returns pane details."""
    fn = resource_functions["tmux://panes/{pane_id}{?socket_name}"]
    result = fn(mcp_pane.pane_id)
    data = json.loads(result)
    assert data["pane_id"] == mcp_pane.pane_id


def test_pane_content_resource(
    resource_functions: dict[str, t.Any], mcp_pane: Pane
) -> None:
    """tmux://panes/{pane_id}/content returns captured text."""
    fn = resource_functions["tmux://panes/{pane_id}/content{?socket_name}"]
    result = fn(mcp_pane.pane_id)
    assert isinstance(result, str)


@pytest.mark.parametrize(
    ("uri", "expected_mime"),
    [
        ("tmux://sessions{?socket_name}", "application/json"),
        ("tmux://sessions/{session_name}{?socket_name}", "application/json"),
        (
            "tmux://sessions/{session_name}/windows{?socket_name}",
            "application/json",
        ),
        (
            "tmux://sessions/{session_name}/windows/{window_index}{?socket_name}",
            "application/json",
        ),
        ("tmux://panes/{pane_id}{?socket_name}", "application/json"),
        ("tmux://panes/{pane_id}/content{?socket_name}", "text/plain"),
    ],
)
def test_hierarchy_resources_advertise_mime_type(uri: str, expected_mime: str) -> None:
    """Each registered hierarchy resource carries its declared mime_type.

    Before this change the resources all returned bare ``json.dumps``
    strings with no MIME annotation — clients had to sniff or assume.
    Declaring mime_type explicitly at registration is what lets the
    JSON resources report as ``application/json`` and the pane-content
    resource report as ``text/plain``.
    """
    import asyncio

    from fastmcp import FastMCP

    mcp = FastMCP(name="test-resource-mime")
    register(mcp)

    resources = asyncio.run(mcp.list_resources())
    templates = asyncio.run(mcp.list_resource_templates())
    # Concrete URIs register as resources; templated URIs like
    # "tmux://sessions/{session_name}..." register as resource templates.
    by_uri: dict[str, t.Any] = {}
    for tpl in templates:
        key = getattr(tpl, "uri_template", None) or getattr(tpl, "uri", None)
        if key is not None:
            by_uri[str(key)] = tpl
    for res in resources:
        key = getattr(res, "uri", None)
        if key is not None:
            by_uri[str(key)] = res
    candidate = by_uri.get(uri)
    assert candidate is not None, f"resource {uri!r} not registered"
    assert candidate.mime_type == expected_mime
