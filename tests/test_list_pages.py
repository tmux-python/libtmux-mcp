"""Functional and schema contracts for bounded hierarchy list tools."""

from __future__ import annotations

import asyncio
import os
import pathlib
import typing as t

import pytest
from fastmcp import Client, FastMCP

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.tools import register_tools
from libtmux_mcp.tools.server_tools import list_servers, list_sessions
from libtmux_mcp.tools.session_tools import list_windows
from libtmux_mcp.tools.window_tools import list_panes

if t.TYPE_CHECKING:
    from libtmux.server import Server
    from libtmux.session import Session


# Per-tool wire-catalog ceiling with room for ordinary description and field
# growth. This catches repeated/expanded union schemas without coupling the
# test to exact bytes or to unrelated tools added elsewhere in the catalog.
_LIST_TOOL_CATALOG_BYTES_CEILING = 12_000


def _numeric_id(value: str | None) -> int:
    """Return the numeric part of a tmux identity for ordering assertions."""
    assert value is not None
    return int(value[1:])


def test_list_sessions_filters_sorts_then_pages(
    mcp_server: Server,
) -> None:
    """Session totals describe filtered rows before deterministic paging."""
    from libtmux_mcp import models

    page_type = getattr(models, "SessionPage", None)
    assert page_type is not None, "SessionPage is not implemented"
    created = [
        mcp_server.new_session(session_name=f"page-session-{suffix}")
        for suffix in ("z", "a", "m")
    ]

    result = list_sessions(
        socket_name=mcp_server.socket_name,
        filters={"session_name__startswith": "page-session-"},
        limit=2,
        offset=1,
    )

    expected_ids = sorted(
        (session.session_id for session in created),
        key=_numeric_id,
    )
    assert isinstance(result, page_type)
    assert [item.session_id for item in result.items] == expected_ids[1:3]
    assert result.total == 3
    assert result.offset == 1
    assert result.limit == 2
    assert result.truncated is False


@pytest.mark.usefixtures("mcp_session")
def test_list_servers_returns_typed_page(
    mcp_server: Server,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server discovery returns a typed page with the advertised defaults."""
    from libtmux_mcp import models

    page_type = getattr(models, "ServerPage", None)
    assert page_type is not None, "ServerPage is not implemented"
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    fixture_socket = (
        pathlib.Path("/tmp")
        / f"tmux-{os.geteuid()}"
        / (mcp_server.socket_name or "default")
    )
    assert fixture_socket.is_socket()

    result = list_servers(extra_socket_paths=[str(fixture_socket)])

    assert isinstance(result, page_type)
    assert [item.socket_path for item in result.items] == [str(fixture_socket)]
    assert result.total == 1
    assert result.offset == 0
    assert result.limit == 100
    assert result.truncated is False


def test_list_windows_defaults_to_summary_and_accepts_full_detail(
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """Window pages default to compact rows and expose full rows on request."""
    from libtmux_mcp import models

    page_type = getattr(models, "WindowPage", None)
    summary_type = getattr(models, "WindowSummary", None)
    assert page_type is not None, "WindowPage is not implemented"
    assert summary_type is not None, "WindowSummary is not implemented"
    mcp_session.new_window(window_name="page-window-z")
    mcp_session.new_window(window_name="page-window-a")

    summary = list_windows(
        session_id=mcp_session.session_id,
        socket_name=mcp_server.socket_name,
        limit=1,
        offset=1,
    )
    full = list_windows(
        session_id=mcp_session.session_id,
        socket_name=mcp_server.socket_name,
        detail="full",
    )

    full_ids = sorted(
        (window.window_id for window in mcp_session.windows),
        key=_numeric_id,
    )
    assert isinstance(summary, page_type)
    assert summary.total == len(full_ids)
    assert summary.truncated is (len(full_ids) > 2)
    assert [item.window_id for item in summary.items] == full_ids[1:2]
    assert all(isinstance(item, summary_type) for item in summary.items)
    assert all(not hasattr(item, "window_layout") for item in summary.items)
    assert [item.window_id for item in full.items] == full_ids
    assert all(isinstance(item, models.WindowInfo) for item in full.items)
    assert all(hasattr(item, "window_layout") for item in full.items)


def test_list_windows_pages_linked_rows_by_window_then_parent(
    mcp_server: Server,
) -> None:
    """Linked-window rows have deterministic parent ordering across pages."""
    origin_session = mcp_server.new_session(session_name="zz-linked-page-origin")
    linked_window = origin_session.active_window
    assert linked_window.window_id is not None
    second_session = mcp_server.new_session(session_name="aa-linked-page-parent")
    assert second_session.session_id is not None
    link_result = mcp_server.cmd(
        "link-window",
        "-s",
        linked_window.window_id,
        "-t",
        f"{second_session.session_id}:9",
    )
    assert link_result.stderr == []

    pages = [
        list_windows(
            socket_name=mcp_server.socket_name,
            filters={"window_id": linked_window.window_id},
            limit=1,
            offset=offset,
        )
        for offset in range(2)
    ]

    expected_session_ids = sorted(
        (origin_session.session_id, second_session.session_id),
        key=_numeric_id,
    )
    assert [page.total for page in pages] == [2, 2]
    assert [page.items[0].session_id for page in pages] == expected_session_ids


def test_list_panes_filters_before_summary_projection_and_paging(
    mcp_server: Server,
    mcp_session: Session,
) -> None:
    """Pane filters can use full metadata before compact rows are projected."""
    from libtmux_mcp import models

    page_type = getattr(models, "PanePage", None)
    summary_type = getattr(models, "PaneSummary", None)
    assert page_type is not None, "PanePage is not implemented"
    assert summary_type is not None, "PaneSummary is not implemented"
    window = mcp_session.active_window
    window.split()
    window.split()
    command = window.panes[0].pane_current_command
    assert command is not None

    summary = list_panes(
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
        filters={"pane_current_command": command},
        limit=1,
    )
    full = list_panes(
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
        filters={"pane_current_command": command},
        detail="full",
    )

    expected_ids = sorted(
        (pane.pane_id for pane in window.panes if pane.pane_current_command == command),
        key=_numeric_id,
    )
    assert isinstance(summary, page_type)
    assert summary.total == len(expected_ids)
    assert summary.truncated is (len(expected_ids) > 1)
    assert [item.pane_id for item in summary.items] == expected_ids[:1]
    assert all(isinstance(item, summary_type) for item in summary.items)
    assert all(not hasattr(item, "pane_current_path") for item in summary.items)
    assert [item.pane_id for item in full.items] == expected_ids
    assert all(isinstance(item, models.PaneInfo) for item in full.items)


@pytest.mark.parametrize(
    ("tool_name", "kwargs"),
    [
        ("list_sessions", {"limit": 0}),
        ("list_servers", {"limit": -1}),
        ("list_windows", {"offset": -1}),
        ("list_panes", {"limit": 0}),
    ],
    ids=["sessions-limit", "servers-limit", "windows-offset", "panes-limit"],
)
def test_list_tools_reject_invalid_direct_python_bounds(
    tool_name: str,
    kwargs: dict[str, int],
) -> None:
    """Every list tool rejects invalid direct-Python page bounds."""
    tools: dict[str, t.Callable[..., t.Any]] = {
        "list_sessions": list_sessions,
        "list_servers": list_servers,
        "list_windows": list_windows,
        "list_panes": list_panes,
    }

    with pytest.raises(ExpectedToolError, match=r"limit.*greater than 0|offset.*0"):
        tools[tool_name](**kwargs)


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("limit", True),
        ("limit", "10"),
        ("offset", False),
        ("offset", 0.5),
    ],
    ids=["limit-bool", "limit-string", "offset-bool", "offset-float"],
)
def test_list_servers_rejects_non_integer_direct_python_bounds(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    parameter: str,
    value: object,
) -> None:
    """The public list path preserves the shared helper's strict types."""
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    kwargs: dict[str, t.Any] = {parameter: value}

    with pytest.raises(ExpectedToolError, match=rf"{parameter}.*integer"):
        list_servers(**kwargs)


@pytest.mark.parametrize(
    "tool",
    [
        pytest.param(list_windows, id="windows"),
        pytest.param(list_panes, id="panes"),
    ],
)
def test_projected_list_tools_reject_invalid_direct_python_detail(
    tool: t.Callable[..., t.Any],
) -> None:
    """Direct callers cannot silently widen an unknown detail projection."""
    with pytest.raises(ExpectedToolError, match=r"Invalid detail.*summary.*full"):
        tool(detail=t.cast("t.Any", "wide"))


def test_list_tool_schemas_publish_bounds_pages_and_projection_widths() -> None:
    """tools/list exposes compact defaults and both typed projection widths."""
    mcp = FastMCP(name="list-page-schema-audit")
    register_tools(mcp)
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    expected_item_descriptions = {
        "list_sessions": {"Serialized tmux session."},
        "list_servers": {"Serialized tmux server info."},
        "list_windows": {
            "Compact tmux window metadata for discovery lists.",
            "Serialized tmux window.",
        },
        "list_panes": {
            "Compact tmux pane metadata for discovery lists.",
            "Serialized tmux pane.",
        },
    }
    for tool_name, item_descriptions in expected_item_descriptions.items():
        tool = tools[tool_name]
        assert tool.parameters["properties"]["limit"]["default"] == 100
        assert tool.parameters["properties"]["offset"]["default"] == 0
        assert tool.output_schema is not None
        assert set(tool.output_schema["properties"]) == {
            "items",
            "total",
            "offset",
            "limit",
            "truncated",
        }
        item_schema = tool.output_schema["properties"]["items"]["items"]
        alternatives = item_schema.get("anyOf", [item_schema])
        assert {schema["description"] for schema in alternatives} == item_descriptions

    for tool_name in ("list_windows", "list_panes"):
        detail = tools[tool_name].parameters["properties"]["detail"]
        assert detail["default"] == "summary"
        assert detail["enum"] == ["summary", "full"]


def test_list_tool_wire_catalog_entries_stay_bounded() -> None:
    """Each list tool keeps a compact catalog entry with schema headroom."""
    mcp = FastMCP(name="list-page-wire-size-audit")
    register_tools(mcp)

    async def _list_tools() -> dict[str, t.Any]:
        async with Client(mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = asyncio.run(_list_tools())
    for tool_name in (
        "list_sessions",
        "list_servers",
        "list_windows",
        "list_panes",
    ):
        payload = tools[tool_name].model_dump_json(
            by_alias=True,
            exclude_none=True,
        )
        payload_bytes = len(payload.encode("utf-8"))
        assert payload_bytes <= _LIST_TOOL_CATALOG_BYTES_CEILING, (
            f"{tool_name} MCP catalog entry is {payload_bytes} bytes; "
            f"ceiling is {_LIST_TOOL_CATALOG_BYTES_CEILING}"
        )


def test_structured_list_pages_bypass_text_response_limiting() -> None:
    """Typed list pages rely on their page bounds, not text truncation."""
    from libtmux_mcp.server import _RESPONSE_LIMITED_TOOLS

    structured_lists = {
        "list_sessions",
        "list_servers",
        "list_windows",
        "list_panes",
    }
    assert structured_lists.isdisjoint(_RESPONSE_LIMITED_TOOLS)
