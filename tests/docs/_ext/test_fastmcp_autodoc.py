"""Tests for fastmcp_autodoc Sphinx extension."""

from __future__ import annotations

import typing as t

import fastmcp_autodoc
import pytest

# ---------------------------------------------------------------------------
# _parse_numpy_params
# ---------------------------------------------------------------------------


class ParseNumpyParamsFixture(t.NamedTuple):
    """Test fixture for NumPy docstring parameter parsing."""

    test_id: str
    docstring: str
    expected: dict[str, str]


PARSE_NUMPY_PARAMS_FIXTURES: list[ParseNumpyParamsFixture] = [
    ParseNumpyParamsFixture(
        test_id="basic",
        docstring=(
            "Do something.\n\n"
            "Parameters\n"
            "----------\n"
            "name : str\n"
            "    The name.\n"
            "\n"
            "Returns\n"
            "-------\n"
            "str\n"
        ),
        expected={"name": "The name."},
    ),
    ParseNumpyParamsFixture(
        test_id="multiple_params",
        docstring=(
            "Do something.\n\n"
            "Parameters\n"
            "----------\n"
            "socket_name : str, optional\n"
            "    tmux socket name.\n"
            "filters : dict or str, optional\n"
            '    Django-style filters (e.g. ``{"key": "val"}``).\n'
            "\n"
            "Returns\n"
            "-------\n"
        ),
        expected={
            "socket_name": "tmux socket name.",
            "filters": 'Django-style filters (e.g. ``{"key": "val"}``).',
        },
    ),
    ParseNumpyParamsFixture(
        test_id="multiline_description",
        docstring=(
            "Summary.\n\n"
            "Parameters\n"
            "----------\n"
            "keys : str\n"
            "    The keys or text to send.\n"
            "    Can span multiple lines.\n"
            "\n"
            "Returns\n"
            "-------\n"
        ),
        expected={"keys": "The keys or text to send. Can span multiple lines."},
    ),
    ParseNumpyParamsFixture(
        test_id="empty_docstring",
        docstring="",
        expected={},
    ),
    ParseNumpyParamsFixture(
        test_id="no_parameters_section",
        docstring="Do something.\n\nReturns\n-------\nstr\n",
        expected={},
    ),
]


@pytest.mark.parametrize(
    PARSE_NUMPY_PARAMS_FIXTURES[0]._fields,
    PARSE_NUMPY_PARAMS_FIXTURES,
    ids=[f.test_id for f in PARSE_NUMPY_PARAMS_FIXTURES],
)
def test_parse_numpy_params(
    test_id: str,
    docstring: str,
    expected: dict[str, str],
) -> None:
    """_parse_numpy_params extracts parameter descriptions."""
    result = fastmcp_autodoc._parse_numpy_params(docstring)
    assert result == expected


# ---------------------------------------------------------------------------
# _first_paragraph
# ---------------------------------------------------------------------------


class FirstParagraphFixture(t.NamedTuple):
    """Test fixture for first paragraph extraction."""

    test_id: str
    docstring: str
    expected: str


FIRST_PARAGRAPH_FIXTURES: list[FirstParagraphFixture] = [
    FirstParagraphFixture(
        test_id="simple",
        docstring="List all tmux sessions.",
        expected="List all tmux sessions.",
    ),
    FirstParagraphFixture(
        test_id="multiline_first_para",
        docstring="Capture the visible contents\nof a tmux pane.\n\nMore detail.",
        expected="Capture the visible contents of a tmux pane.",
    ),
    FirstParagraphFixture(
        test_id="empty",
        docstring="",
        expected="",
    ),
]


@pytest.mark.parametrize(
    FIRST_PARAGRAPH_FIXTURES[0]._fields,
    FIRST_PARAGRAPH_FIXTURES,
    ids=[f.test_id for f in FIRST_PARAGRAPH_FIXTURES],
)
def test_first_paragraph(
    test_id: str,
    docstring: str,
    expected: str,
) -> None:
    """_first_paragraph extracts the first paragraph."""
    result = fastmcp_autodoc._first_paragraph(docstring)
    assert result == expected


# ---------------------------------------------------------------------------
# _format_annotation
# ---------------------------------------------------------------------------


class FormatAnnotationFixture(t.NamedTuple):
    """Test fixture for annotation formatting."""

    test_id: str
    annotation: t.Any
    strip_none: bool
    expected: str


FORMAT_ANNOTATION_FIXTURES: list[FormatAnnotationFixture] = [
    FormatAnnotationFixture(
        test_id="string_with_none",
        annotation="str | None",
        strip_none=False,
        expected="str | None",
    ),
    FormatAnnotationFixture(
        test_id="string_strip_none",
        annotation="str | None",
        strip_none=True,
        expected="str",
    ),
    FormatAnnotationFixture(
        test_id="complex_strip_none",
        annotation="dict[str, str] | str | None",
        strip_none=True,
        expected="dict[str, str] | str",
    ),
    FormatAnnotationFixture(
        test_id="no_none_strip_noop",
        annotation="str",
        strip_none=True,
        expected="str",
    ),
    FormatAnnotationFixture(
        test_id="literal_cleanup",
        annotation="t.Literal['server', 'session', 'window', 'pane'] | None",
        strip_none=True,
        expected="'server', 'session', 'window', 'pane'",
    ),
    FormatAnnotationFixture(
        test_id="literal_cleanup_no_strip",
        annotation="t.Literal['server', 'session', 'window', 'pane']",
        strip_none=False,
        expected="'server', 'session', 'window', 'pane'",
    ),
    FormatAnnotationFixture(
        test_id="literal_no_prefix",
        annotation="Literal['before', 'after']",
        strip_none=False,
        expected="'before', 'after'",
    ),
    FormatAnnotationFixture(
        test_id="int_type",
        annotation=int,
        strip_none=False,
        expected="int",
    ),
    FormatAnnotationFixture(
        test_id="empty",
        annotation="",
        strip_none=False,
        expected="",
    ),
]


@pytest.mark.parametrize(
    FORMAT_ANNOTATION_FIXTURES[0]._fields,
    FORMAT_ANNOTATION_FIXTURES,
    ids=[f.test_id for f in FORMAT_ANNOTATION_FIXTURES],
)
def test_format_annotation(
    test_id: str,
    annotation: t.Any,
    strip_none: bool,
    expected: str,
) -> None:
    """_format_annotation formats type annotations correctly."""
    import inspect

    if annotation == "":
        annotation = inspect.Parameter.empty
        expected = ""

    result = fastmcp_autodoc._format_annotation(annotation, strip_none=strip_none)
    assert result == expected


# ---------------------------------------------------------------------------
# _ToolCollector
# ---------------------------------------------------------------------------


def test_tool_collector_captures_registrations() -> None:
    """_ToolCollector captures tool metadata from register() calls."""
    collector = fastmcp_autodoc._ToolCollector()
    collector._current_module = "server_tools"

    @collector.tool(
        title="List Sessions",
        annotations={"readOnlyHint": True},
        tags={"readonly"},
    )
    def list_sessions(socket_name: str | None = None) -> list[str]:
        """List all tmux sessions.

        Parameters
        ----------
        socket_name : str, optional
            tmux socket name.

        Returns
        -------
        list[str]
        """
        return []

    assert len(collector.tools) == 1
    tool = collector.tools[0]
    assert tool.name == "list_sessions"
    assert tool.title == "List Sessions"
    assert tool.safety == "readonly"
    assert tool.area == "sessions"
    assert tool.module_name == "server_tools"
    assert len(tool.params) == 1
    assert tool.params[0].name == "socket_name"
    assert tool.params[0].required is False
    assert tool.params[0].description == "tmux socket name."


def test_tool_collector_safety_tiers() -> None:
    """_ToolCollector correctly determines safety tier from tags."""
    collector = fastmcp_autodoc._ToolCollector()
    collector._current_module = "test_tools"

    @collector.tool(tags={"readonly"})
    def read_tool() -> str:
        """Read."""
        return ""

    @collector.tool(tags={"mutating"})
    def write_tool() -> str:
        """Write."""
        return ""

    @collector.tool(tags={"destructive"})
    def destroy_tool() -> str:
        """Destroy."""
        return ""

    assert collector.tools[0].safety == "readonly"
    assert collector.tools[1].safety == "mutating"
    assert collector.tools[2].safety == "destructive"


def test_tool_collector_strips_none_for_optional_params() -> None:
    """Optional parameters should have | None stripped from type."""
    collector = fastmcp_autodoc._ToolCollector()
    collector._current_module = "test_tools"

    @collector.tool(tags={"readonly"})
    def my_tool(
        required_param: str,
        optional_param: str | None = None,
    ) -> str:
        """Test.

        Parameters
        ----------
        required_param : str
            Required.
        optional_param : str, optional
            Optional.

        Returns
        -------
        str
        """
        return ""

    tool = collector.tools[0]
    required = next(p for p in tool.params if p.name == "required_param")
    optional = next(p for p in tool.params if p.name == "optional_param")

    assert required.required is True
    assert "None" not in required.type_str or required.type_str == "str"

    assert optional.required is False
    # | None should be stripped for optional params
    assert optional.type_str == "str"


# ---------------------------------------------------------------------------
# _make_table
# ---------------------------------------------------------------------------


def test_make_table_structure() -> None:
    """_make_table creates proper docutils table node hierarchy."""
    from docutils import nodes

    table = fastmcp_autodoc._make_table(
        headers=["Name", "Type"],
        rows=[["foo", "str"], ["bar", "int"]],
    )

    assert isinstance(table, nodes.table)
    tgroup = table[0]
    assert isinstance(tgroup, nodes.tgroup)
    assert tgroup["cols"] == 2

    # Header
    thead = tgroup.children[2]  # after 2 colspecs
    assert isinstance(thead, nodes.thead)
    header_row = thead[0]
    assert len(header_row) == 2

    # Body
    tbody = tgroup.children[3]
    assert isinstance(tbody, nodes.tbody)
    assert len(tbody) == 2  # 2 data rows


def test_make_table_with_node_cells() -> None:
    """_make_table handles Node objects as cell values."""
    from docutils import nodes

    literal = nodes.literal("", "code")
    para = nodes.paragraph("", "")
    para += literal

    table = fastmcp_autodoc._make_table(
        headers=["Col"],
        rows=[[para]],
    )

    # Just check the table built without error
    assert isinstance(table, nodes.table)


# ---------------------------------------------------------------------------
# _safety_badge
# ---------------------------------------------------------------------------


def test_make_type_cell_splits_union() -> None:
    """_make_type_cell splits union types into comma-separated literals."""
    from docutils import nodes

    para = fastmcp_autodoc._make_type_cell("dict[str, str] | str")
    literals = [c for c in para.children if isinstance(c, nodes.literal)]
    texts = [c.astext() for c in literals]
    assert texts == ["dict[str, str]", "str"]

    # Separators should be Text nodes with ", "
    text_nodes = [c for c in para.children if isinstance(c, nodes.Text)]
    assert any(", " in c.astext() for c in text_nodes)


def test_make_type_cell_splits_literal_values() -> None:
    """_make_type_cell splits quoted literal values into separate literals."""
    from docutils import nodes

    para = fastmcp_autodoc._make_type_cell("'server', 'session', 'window'")
    literals = [c for c in para.children if isinstance(c, nodes.literal)]
    texts = [c.astext() for c in literals]
    assert texts == ["'server'", "'session'", "'window'"]


def test_make_type_cell_single_type() -> None:
    """_make_type_cell handles single types without splitting."""
    from docutils import nodes

    para = fastmcp_autodoc._make_type_cell("str")
    literals = [c for c in para.children if isinstance(c, nodes.literal)]
    assert len(literals) == 1
    assert literals[0].astext() == "str"


def test_safety_badge_classes() -> None:
    """_safety_badge creates inline nodes with correct CSS classes."""
    badge = fastmcp_autodoc._safety_badge("readonly")
    assert "sd-bg-success" in badge["classes"]

    badge = fastmcp_autodoc._safety_badge("mutating")
    assert "sd-bg-warning" in badge["classes"]

    badge = fastmcp_autodoc._safety_badge("destructive")
    assert "sd-bg-danger" in badge["classes"]


# ---------------------------------------------------------------------------
# SECTION_BADGE_MAP + _add_section_badges
# ---------------------------------------------------------------------------


def test_section_badge_map_headings() -> None:
    """SECTION_BADGE_MAP maps group headings to safety tiers."""
    m = fastmcp_autodoc.SECTION_BADGE_MAP
    assert m["Inspect"] == "readonly"
    assert m["Act"] == "mutating"
    assert m["Destroy"] == "destructive"


def test_add_section_badges_appends_badge_to_title() -> None:
    """_add_section_badges appends a safety badge to matching titles."""
    from docutils import nodes
    from docutils.frontend import OptionParser
    from docutils.utils import new_document

    settings = OptionParser(components=()).get_default_values()
    doc = new_document("test", settings)

    section = nodes.section(ids=["inspect"])
    title = nodes.title("", "Inspect")
    section += title
    doc += section

    # Simulate the handler — it expects (app, doctree, fromdocname)
    # but only uses doctree, so pass None for the others.
    fastmcp_autodoc._add_section_badges(None, doc, "")

    # Title should now have 3 children: Text("Inspect"), Text(" "), inline(badge)
    assert len(title.children) == 3
    badge = title.children[2]
    assert isinstance(badge, nodes.inline)
    assert "sd-bg-success" in badge["classes"]
    assert badge.astext() == "readonly"


def test_add_section_badges_preserves_section_id() -> None:
    """_add_section_badges does not change the section ID."""
    from docutils import nodes
    from docutils.frontend import OptionParser
    from docutils.utils import new_document

    settings = OptionParser(components=()).get_default_values()
    doc = new_document("test", settings)

    section = nodes.section(ids=["inspect"])
    section += nodes.title("", "Inspect")
    doc += section

    fastmcp_autodoc._add_section_badges(None, doc, "")

    assert section["ids"] == ["inspect"]


def test_add_section_badges_ignores_non_matching() -> None:
    """_add_section_badges leaves non-matching headings untouched."""
    from docutils import nodes
    from docutils.frontend import OptionParser
    from docutils.utils import new_document

    settings = OptionParser(components=()).get_default_values()
    doc = new_document("test", settings)

    section = nodes.section(ids=["overview"])
    title = nodes.title("", "Overview")
    section += title
    doc += section

    fastmcp_autodoc._add_section_badges(None, doc, "")

    # Title should still have only the original text child
    assert len(title.children) == 1
    assert title.astext() == "Overview"


# ---------------------------------------------------------------------------
# Integration: collect real tools
# ---------------------------------------------------------------------------


def test_collect_real_tools() -> None:
    """Collecting tools from libtmux_mcp source produces expected results."""
    collector = fastmcp_autodoc._ToolCollector()

    tool_modules = [
        "server_tools",
        "session_tools",
        "window_tools",
        "pane_tools",
        "option_tools",
        "env_tools",
    ]

    import importlib

    for mod_name in tool_modules:
        collector._current_module = mod_name
        mod = importlib.import_module(f"libtmux_mcp.tools.{mod_name}")
        mod.register(collector)

    tools = {t.name: t for t in collector.tools}

    # Should have all expected tools
    assert "list_sessions" in tools
    assert "capture_pane" in tools
    assert "send_keys" in tools
    assert "kill_server" in tools
    assert "show_option" in tools
    assert "show_environment" in tools

    # Safety tiers should be correct
    assert tools["list_sessions"].safety == "readonly"
    assert tools["send_keys"].safety == "mutating"
    assert tools["kill_server"].safety == "destructive"

    # Parameters should be extracted
    ls = tools["list_sessions"]
    param_names = [p.name for p in ls.params]
    assert "socket_name" in param_names
    assert "filters" in param_names

    # Descriptions should be parsed from docstrings
    socket_param = next(p for p in ls.params if p.name == "socket_name")
    assert "socket" in socket_param.description.lower()

    # Optional params should have | None stripped
    assert socket_param.type_str == "str"
    assert socket_param.required is False


def test_collect_real_tools_total_count() -> None:
    """All 27 tools should be collected."""
    collector = fastmcp_autodoc._ToolCollector()

    import importlib

    for mod_name in [
        "server_tools",
        "session_tools",
        "window_tools",
        "pane_tools",
        "option_tools",
        "env_tools",
    ]:
        collector._current_module = mod_name
        mod = importlib.import_module(f"libtmux_mcp.tools.{mod_name}")
        mod.register(collector)

    assert len(collector.tools) == 27
