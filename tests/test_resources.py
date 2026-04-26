"""Tests for libtmux MCP resources."""

from __future__ import annotations

import json
import typing as t

import pytest

from libtmux_mcp.resources.hierarchy import register
from libtmux_mcp.resources.reference import register as register_reference

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


def test_every_hierarchy_resource_returns_str(
    resource_functions: dict[str, t.Any],
    mcp_session: Session,
    mcp_pane: Pane,
) -> None:
    """Every registered hierarchy resource returns a ``str``.

    Regression guard mirroring the read-heavy tool shape tests —
    resources wire to MCP clients as raw body strings (JSON text or
    plain text), so a future refactor that accidentally returns a
    dict or Pydantic instance would break the MCP resource surface.
    This parametrized test fails loudly on that drift.
    """
    invocations: list[tuple[str, tuple[t.Any, ...]]] = [
        ("tmux://sessions{?socket_name}", ()),
        (
            "tmux://sessions/{session_name}{?socket_name}",
            (mcp_session.session_name,),
        ),
        (
            "tmux://sessions/{session_name}/windows{?socket_name}",
            (mcp_session.session_name,),
        ),
        ("tmux://panes/{pane_id}{?socket_name}", (mcp_pane.pane_id,)),
        ("tmux://panes/{pane_id}/content{?socket_name}", (mcp_pane.pane_id,)),
    ]
    for uri, args in invocations:
        fn = resource_functions[uri]
        result = fn(*args)
        assert isinstance(result, str), f"{uri} returned {type(result).__name__}"


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


# ---------------------------------------------------------------------------
# Reference resources (static catalogs, no tmux server interaction)
# ---------------------------------------------------------------------------


@pytest.fixture
def reference_resource_functions() -> dict[str, t.Any]:
    """Capture reference-module resource closures by URI.

    Mirrors ``resource_functions`` but registers
    :mod:`libtmux_mcp.resources.reference` instead of ``hierarchy``.
    Reference resources are static and need no tmux fixtures.
    """
    functions: dict[str, t.Any] = {}

    class MockMCP:
        def resource(self, uri: str, **kwargs: t.Any) -> t.Any:
            def decorator(fn: t.Any) -> t.Any:
                functions[uri] = fn
                return fn

            return decorator

    register_reference(MockMCP())  # type: ignore[arg-type]
    return functions


def test_format_string_reference_returns_markdown(
    reference_resource_functions: dict[str, t.Any],
) -> None:
    """tmux://reference/format-strings returns non-empty Markdown.

    The agent's reason for pulling this resource is to recover from an
    unfamiliar ``#{...}`` token without burning a ``display_message``
    round-trip. The body must therefore (a) be present and (b) name
    the format strings most likely to confuse — pane / window /
    session ID forms and the ``#{?cond,then,else}`` conditional.
    """
    fn = reference_resource_functions["tmux://reference/format-strings"]
    body = fn()
    assert isinstance(body, str)
    assert body.strip(), "format-string reference body is empty"
    # Spot-check the highest-traffic catalog entries — if any of these
    # vanish, the reference is failing at its job.
    assert "#{pane_id}" in body
    assert "#{window_id}" in body
    assert "#{session_id}" in body
    assert "#{?cond,then,else}" in body


def test_format_string_reference_advertises_markdown_mime() -> None:
    """tmux://reference/format-strings is registered with text/markdown.

    Concrete URIs (no ``{...}`` template params) register as resources,
    not resource templates — they show up under ``mcp.list_resources()``
    rather than ``mcp.list_resource_templates()``.
    """
    import asyncio

    from fastmcp import FastMCP

    mcp = FastMCP(name="test-reference-mime")
    register_reference(mcp)

    resources = asyncio.run(mcp.list_resources())
    by_uri = {str(getattr(r, "uri", "")): r for r in resources}
    target = by_uri.get("tmux://reference/format-strings")
    assert target is not None, "format-strings reference not registered"
    assert target.mime_type == "text/markdown"
