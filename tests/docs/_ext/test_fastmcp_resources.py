"""Tests for fastmcp_autodoc resource collection and nodes."""

from __future__ import annotations

import typing as t

import fastmcp_autodoc
import pytest

# ---------------------------------------------------------------------------
# _ResourceCollector
# ---------------------------------------------------------------------------


def test_resource_collector_captures_registrations() -> None:
    """_ResourceCollector captures resource metadata from register() calls."""
    collector = fastmcp_autodoc._ResourceCollector()
    collector._current_module = "hierarchy"

    @collector.resource("tmux://sessions{?socket_name}", title="All Sessions")
    def get_sessions(socket_name: str | None = None) -> str:
        """List all tmux sessions.

        Parameters
        ----------
        socket_name : str, optional
            tmux socket name.

        Returns
        -------
        str
        """
        return ""

    assert len(collector.resources) == 1
    resource = collector.resources[0]
    assert resource.name == "get_sessions"
    assert resource.qualified_name == "hierarchy.get_sessions"
    assert resource.title == "All Sessions"
    assert resource.uri_template == "tmux://sessions{?socket_name}"
    assert len(resource.params) == 1
    assert resource.params[0].name == "socket_name"
    assert resource.params[0].required is False


def test_resource_collector_default_title() -> None:
    """_ResourceCollector uses func name as default title."""
    collector = fastmcp_autodoc._ResourceCollector()
    collector._current_module = "hierarchy"

    @collector.resource("tmux://sessions")
    def get_sessions() -> str:
        """List sessions."""
        return ""

    assert collector.resources[0].title == "Get Sessions"


# ---------------------------------------------------------------------------
# Real resource collection
# ---------------------------------------------------------------------------


def test_collect_real_resources_total_count() -> None:
    """All 6 resources should be collected from hierarchy.py."""
    collector = fastmcp_autodoc._ResourceCollector()
    collector._current_module = "hierarchy"

    import importlib

    mod = importlib.import_module("libtmux_mcp.resources.hierarchy")
    mod.register(collector)

    assert len(collector.resources) == 6


class RealResourceFixture(t.NamedTuple):
    """Test fixture for real resource verification."""

    test_id: str
    name: str
    uri_template: str
    title: str


REAL_RESOURCE_FIXTURES: list[RealResourceFixture] = [
    RealResourceFixture(
        test_id="get_sessions",
        name="get_sessions",
        uri_template="tmux://sessions{?socket_name}",
        title="All Sessions",
    ),
    RealResourceFixture(
        test_id="get_session",
        name="get_session",
        uri_template="tmux://sessions/{session_name}{?socket_name}",
        title="Session Detail",
    ),
    RealResourceFixture(
        test_id="get_session_windows",
        name="get_session_windows",
        uri_template="tmux://sessions/{session_name}/windows{?socket_name}",
        title="Session Windows",
    ),
    RealResourceFixture(
        test_id="get_window",
        name="get_window",
        uri_template="tmux://sessions/{session_name}/windows/{window_index}{?socket_name}",
        title="Window Detail",
    ),
    RealResourceFixture(
        test_id="get_pane",
        name="get_pane",
        uri_template="tmux://panes/{pane_id}{?socket_name}",
        title="Pane Detail",
    ),
    RealResourceFixture(
        test_id="get_pane_content",
        name="get_pane_content",
        uri_template="tmux://panes/{pane_id}/content{?socket_name}",
        title="Pane Content",
    ),
]


@pytest.mark.parametrize(
    REAL_RESOURCE_FIXTURES[0]._fields,
    REAL_RESOURCE_FIXTURES,
    ids=[f.test_id for f in REAL_RESOURCE_FIXTURES],
)
def test_collect_real_resource_details(
    test_id: str,
    name: str,
    uri_template: str,
    title: str,
) -> None:
    """Real resources have correct URI templates and titles."""
    collector = fastmcp_autodoc._ResourceCollector()
    collector._current_module = "hierarchy"

    import importlib

    mod = importlib.import_module("libtmux_mcp.resources.hierarchy")
    mod.register(collector)

    resources = {r.name: r for r in collector.resources}
    resource = resources[name]
    assert resource.uri_template == uri_template
    assert resource.title == title


# ---------------------------------------------------------------------------
# _resource_badge_node
# ---------------------------------------------------------------------------


def test_resource_badge_classes() -> None:
    """_resource_badge creates badge node with correct CSS classes."""
    badge = fastmcp_autodoc._resource_badge()
    assert isinstance(badge, fastmcp_autodoc._resource_badge_node)
    assert "sd-bg-info" in badge["classes"]
    assert "sd-bg-text-info" in badge["classes"]
    assert "sd-sphinx-override" in badge["classes"]
    assert "sd-badge" in badge["classes"]
    assert badge.astext() == "resource"


# ---------------------------------------------------------------------------
# Resource roles
# ---------------------------------------------------------------------------


def test_resource_role_creates_placeholder() -> None:
    """_resource_role creates _resource_ref_placeholder with show_badge=True."""
    result_nodes, _messages = fastmcp_autodoc._resource_role(
        "resource", ":resource:`get-sessions`", "get-sessions", 1, None
    )
    assert len(result_nodes) == 1
    node = result_nodes[0]
    assert isinstance(node, fastmcp_autodoc._resource_ref_placeholder)
    assert node["reftarget"] == "get-sessions"
    assert node["show_badge"] is True


def test_resourceref_role_creates_placeholder() -> None:
    """_resourceref_role creates _resource_ref_placeholder with show_badge=False."""
    result_nodes, _messages = fastmcp_autodoc._resourceref_role(
        "resourceref", ":resourceref:`get-sessions`", "get-sessions", 1, None
    )
    assert len(result_nodes) == 1
    node = result_nodes[0]
    assert isinstance(node, fastmcp_autodoc._resource_ref_placeholder)
    assert node["reftarget"] == "get-sessions"
    assert node["show_badge"] is False


def test_resource_role_normalizes_underscores() -> None:
    """_resource_role converts underscores to hyphens in target."""
    result_nodes, _ = fastmcp_autodoc._resource_role(
        "resource", ":resource:`get_sessions`", "get_sessions", 1, None
    )
    assert result_nodes[0]["reftarget"] == "get-sessions"
