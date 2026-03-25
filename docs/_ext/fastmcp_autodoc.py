"""Sphinx extension for autodocumenting FastMCP tools.

Builds documentation directly from docutils/Sphinx node API — no text
generation or markdown parsing. Tables, sections, and cross-references
are all proper doctree nodes.

Provides two directives:

- ``fastmcp-tool``: Autodocument a single MCP tool function.
  Creates a section (visible in ToC) with safety badge, parameter table,
  and return type.
- ``fastmcp-toolsummary``: Generate a summary table of all tools grouped
  by safety tier.

Usage in MyST::

    ```{fastmcp-tool} server_tools.list_sessions
    ```

    ```{fastmcp-toolsummary}
    ```
"""

from __future__ import annotations

import importlib
import inspect
import re
import typing as t
from dataclasses import dataclass

from docutils import nodes
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective

if t.TYPE_CHECKING:
    from sphinx.domains.std import StandardDomain
    from sphinx.util.typing import ExtensionMetadata

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AREA_MAP: dict[str, str] = {
    "server_tools": "sessions",
    "session_tools": "sessions",
    "window_tools": "windows",
    "pane_tools": "panes",
    "option_tools": "options",
    "env_tools": "options",
}

TAG_READONLY = "readonly"
TAG_MUTATING = "mutating"
TAG_DESTRUCTIVE = "destructive"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParamInfo:
    """Extracted parameter information."""

    name: str
    type_str: str
    required: bool
    default: str
    description: str


@dataclass
class ToolInfo:
    """Collected metadata for a single MCP tool."""

    name: str
    title: str
    module_name: str
    area: str
    safety: str
    annotations: dict[str, bool]
    func: t.Callable[..., t.Any]
    docstring: str
    params: list[ParamInfo]
    return_annotation: str


# ---------------------------------------------------------------------------
# Docstring + signature parsing
# ---------------------------------------------------------------------------


def _parse_numpy_params(docstring: str) -> dict[str, str]:
    """Extract parameter descriptions from NumPy-style docstring."""
    params: dict[str, str] = {}
    if not docstring:
        return params

    lines = docstring.split("\n")
    in_params = False
    current_param: str | None = None
    current_desc: list[str] = []

    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if stripped == "Parameters":
            in_params = True
            continue
        if in_params and stripped.startswith("---"):
            continue
        if in_params and stripped in (
            "Returns",
            "Raises",
            "Notes",
            "Examples",
            "See Also",
        ):
            if current_param:
                params[current_param] = " ".join(current_desc).strip()
            break
        if in_params and not stripped:
            continue

        if in_params:
            param_match = re.match(r"^(\w+)\s*:", stripped)
            if param_match and indent == 0:
                if current_param:
                    params[current_param] = " ".join(current_desc).strip()
                current_param = param_match.group(1)
                current_desc = []
            elif current_param and indent > 0:
                current_desc.append(stripped)

    if current_param:
        params[current_param] = " ".join(current_desc).strip()

    return params


def _first_paragraph(docstring: str) -> str:
    """Extract the first paragraph from a docstring."""
    if not docstring:
        return ""
    paragraphs = docstring.strip().split("\n\n")
    return paragraphs[0].strip().replace("\n", " ")


def _format_annotation(ann: t.Any, *, strip_none: bool = False) -> str:
    """Format a type annotation as a readable string.

    Parameters
    ----------
    ann : Any
        The annotation to format.
    strip_none : bool
        If True, remove ``| None`` from union types. Useful when the
        parameter is already marked as optional.
    """
    if ann is inspect.Parameter.empty:
        return ""
    if isinstance(ann, str):
        result = ann
        # Clean up t.Literal['a', 'b'] or Literal['a', 'b'] → 'a', 'b'
        result = re.sub(
            r"(?:t\.)?Literal\[([^\]]+)\]",
            lambda m: m.group(1),
            result,
        )
        if strip_none:
            result = re.sub(r"\s*\|\s*None\b", "", result).strip()
        return result
    if hasattr(ann, "__name__"):
        return str(ann.__name__)
    return str(ann).replace("typing.", "")


def _extract_params(func: t.Callable[..., t.Any]) -> list[ParamInfo]:
    """Extract parameter info from function signature + docstring."""
    sig = inspect.signature(func)
    doc_params = _parse_numpy_params(func.__doc__ or "")
    params: list[ParamInfo] = []

    for name, param in sig.parameters.items():
        is_optional = param.default != inspect.Parameter.empty
        type_str = _format_annotation(
            param.annotation,
            strip_none=is_optional,
        )

        if is_optional:
            if param.default is None:
                default_str = "None"
            elif isinstance(param.default, bool):
                default_str = str(param.default)
            elif isinstance(param.default, str):
                default_str = repr(param.default)
            else:
                default_str = str(param.default)
            required = False
        else:
            default_str = ""
            required = True

        params.append(
            ParamInfo(
                name=name,
                type_str=type_str,
                required=required,
                default=default_str,
                description=doc_params.get(name, ""),
            )
        )

    return params


# ---------------------------------------------------------------------------
# Node construction helpers
# ---------------------------------------------------------------------------


def _make_table(
    headers: list[str],
    rows: list[list[str | nodes.Node]],
    col_widths: list[int] | None = None,
) -> nodes.table:
    """Build a docutils table node from headers and rows."""
    ncols = len(headers)
    if col_widths is None:
        col_widths = [100 // ncols] * ncols

    table = nodes.table("")
    tgroup = nodes.tgroup("", cols=ncols)
    table += tgroup

    for width in col_widths:
        tgroup += nodes.colspec("", colwidth=width)

    # Header row
    thead = nodes.thead("")
    header_row = nodes.row("")
    for header in headers:
        entry = nodes.entry("")
        entry += nodes.paragraph("", header)
        header_row += entry
    thead += header_row
    tgroup += thead

    # Body rows
    tbody = nodes.tbody("")
    for row_data in rows:
        row = nodes.row("")
        for cell in row_data:
            entry = nodes.entry("")
            if isinstance(cell, nodes.Node):
                entry += cell
            else:
                entry += nodes.paragraph("", str(cell))
            row += entry
        tbody += row
    tgroup += tbody

    return table


def _make_literal(text: str) -> nodes.literal:
    """Create an inline code literal node."""
    return nodes.literal("", text)


def _make_para(*children: nodes.Node | str) -> nodes.paragraph:
    """Create a paragraph from mixed text and node children."""
    para = nodes.paragraph("")
    for child in children:
        if isinstance(child, str):
            para += nodes.Text(child)
        else:
            para += child
    return para


def _parse_rst_inline(
    text: str,
    state: t.Any,
    lineno: int,
) -> nodes.paragraph:
    """Parse a string containing RST inline markup into a paragraph node.

    Handles ``code``, *emphasis*, **strong**, :role:`ref`, etc.
    """
    parsed_nodes, _messages = state.inline_text(text, lineno)
    para = nodes.paragraph("")
    para += parsed_nodes
    return para


def _make_type_cell(type_str: str) -> nodes.paragraph:
    """Render a type annotation as comma-separated code literals.

    ``dict[str, str] | str`` becomes ``dict[str, str]``, ``str``
    ``'server', 'session', 'window'`` becomes ``'server'``, ``'session'``, ...
    — each part in its own <code> element so they wrap cleanly.
    """
    # Split on | for union types
    parts = [p.strip() for p in type_str.split("|")]

    # Further split quoted literal values: 'a', 'b', 'c'
    expanded: list[str] = []
    for part in parts:
        if re.match(r"^'[^']*'(\s*,\s*'[^']*')+$", part):
            # Multiple quoted values like 'server', 'session', 'window'
            expanded.extend(v.strip() for v in part.split(","))
        else:
            expanded.append(part)

    para = nodes.paragraph("")
    for i, part in enumerate(expanded):
        if i > 0:
            para += nodes.Text(", ")
        para += nodes.literal("", part)
    return para


def _make_type_cell_smart(
    type_str: str,
) -> tuple[nodes.paragraph | str, bool]:
    """Render a type annotation, detecting enum-only types.

    Returns (node, is_enum). If the type is purely quoted literal
    values, returns ``enum`` as the type and True so the caller
    can append the values to the description column instead.
    """
    if not type_str:
        return ("", False)

    parts = [p.strip() for p in type_str.split("|")]

    # Check if ALL parts are quoted strings (Literal enum values)
    all_quoted = all(re.match(r"^'[^']*'$", p) for p in parts)
    # Also handle comma-separated quoted values from Literal cleanup
    if not all_quoted and len(parts) == 1:
        sub = [s.strip() for s in parts[0].split(",")]
        all_quoted = len(sub) > 1 and all(re.match(r"^'[^']*'$", s) for s in sub)

    if all_quoted:
        return (_make_para(_make_literal("enum")), True)

    return (_make_type_cell(type_str), False)


def _extract_enum_values(type_str: str) -> list[str]:
    """Extract individual enum values from a Literal type string."""
    parts = [p.strip() for p in type_str.split("|")]
    values: list[str] = []
    for part in parts:
        for sub in part.split(","):
            sub = sub.strip()
            if re.match(r"^'[^']*'$", sub):
                values.append(sub)
    return values


def _safety_badge(safety: str) -> nodes.inline:
    """Create a colored safety badge node."""
    _base = ["sd-sphinx-override", "sd-badge"]
    classes = {
        "readonly": [*_base, "sd-bg-success", "sd-bg-text-success"],
        "mutating": [*_base, "sd-bg-warning", "sd-bg-text-warning"],
        "destructive": [*_base, "sd-bg-danger", "sd-bg-text-danger"],
    }
    badge = nodes.inline("", safety, classes=classes.get(safety, []))
    return badge


# ---------------------------------------------------------------------------
# Tool collection (runs at builder-inited)
# ---------------------------------------------------------------------------


class _ToolCollector:
    """Mock FastMCP that captures tool registrations."""

    def __init__(self) -> None:
        self.tools: list[ToolInfo] = []
        self._current_module: str = ""

    def tool(
        self,
        title: str = "",
        annotations: dict[str, bool] | None = None,
        tags: set[str] | None = None,
    ) -> t.Callable[[t.Callable[..., t.Any]], t.Callable[..., t.Any]]:
        annotations = annotations or {}
        tags = tags or set()

        def decorator(func: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
            if TAG_DESTRUCTIVE in tags:
                safety = "destructive"
            elif TAG_MUTATING in tags:
                safety = "mutating"
            else:
                safety = "readonly"

            module_name = self._current_module
            area = AREA_MAP.get(module_name, module_name.replace("_tools", ""))

            self.tools.append(
                ToolInfo(
                    name=func.__name__,
                    title=title or func.__name__.replace("_", " ").title(),
                    module_name=module_name,
                    area=area,
                    safety=safety,
                    annotations=annotations,
                    func=func,
                    docstring=func.__doc__ or "",
                    params=_extract_params(func),
                    return_annotation=_format_annotation(
                        inspect.signature(func).return_annotation,
                    ),
                )
            )
            return func

        return decorator


def _collect_tools(app: Sphinx) -> None:
    """Collect tool metadata from libtmux_mcp source at build time."""
    collector = _ToolCollector()

    tool_modules = [
        "server_tools",
        "session_tools",
        "window_tools",
        "pane_tools",
        "option_tools",
        "env_tools",
    ]

    for mod_name in tool_modules:
        collector._current_module = mod_name
        try:
            mod = importlib.import_module(f"libtmux_mcp.tools.{mod_name}")
            if hasattr(mod, "register"):
                mod.register(collector)
        except Exception:
            pass  # Module not importable during docs build

    app.env.fastmcp_tools = {tool.name: tool for tool in collector.tools}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Directives
# ---------------------------------------------------------------------------


class FastMCPToolDirective(SphinxDirective):
    """Autodocument a single MCP tool as a proper section with table.

    Creates a section node (visible in ToC) containing:
    - Safety badge + one-line description
    - Parameter table (headers: Parameter, Type, Required, Default, Description)
    - Return type

    Usage::

        ```{fastmcp-tool} server_tools.list_sessions
        ```
    """

    required_arguments = 1
    optional_arguments = 0
    has_content = True
    final_argument_whitespace = False

    def run(self) -> list[nodes.Node]:
        """Build tool section header nodes."""
        arg = self.arguments[0]
        func_name = arg.split(".")[-1] if "." in arg else arg

        tools: dict[str, ToolInfo] = getattr(self.env, "fastmcp_tools", {})
        tool = tools.get(func_name)

        if tool is None:
            return [
                self.state.document.reporter.warning(
                    f"fastmcp-tool: tool '{func_name}' not found. "
                    f"Available: {', '.join(sorted(tools.keys()))}",
                    line=self.lineno,
                )
            ]

        return self._build_tool_section(tool)

    def _build_tool_section(self, tool: ToolInfo) -> list[nodes.Node]:
        """Build section header: title, badge, and description only.

        The parameter table is emitted separately by
        ``FastMCPToolInputDirective`` so that hand-written judgment
        content (Use when / Avoid when / examples) can appear between
        the header and the table in the source file.
        """
        document = self.state.document

        # Section with anchor ID
        section_id = tool.name.replace("_", "-")
        section = nodes.section()
        section["ids"].append(section_id)
        document.note_explicit_target(section)

        # Title: tool name + safety badge inline
        title_node = nodes.title("", "")
        title_node += nodes.literal("", tool.name)
        title_node += nodes.Text(" ")
        title_node += _safety_badge(tool.safety)
        section += title_node

        # Description paragraph
        first_para = _first_paragraph(tool.docstring)
        desc_para = _parse_rst_inline(first_para, self.state, self.lineno)
        section += desc_para

        # Returns (promoted — high-signal for tool selection)
        if tool.return_annotation:
            section += _make_para(
                nodes.strong("", "Returns: "),
                _make_literal(tool.return_annotation),
            )

        return [section]


class FastMCPToolInputDirective(SphinxDirective):
    """Emit the parameter table and return type for a tool.

    Place this AFTER hand-written judgment content so the table
    appears at the end of the tool section.

    Usage::

        ```{fastmcp-tool-input} server_tools.list_sessions
        ```
    """

    required_arguments = 1
    optional_arguments = 0
    has_content = False

    def run(self) -> list[nodes.Node]:
        """Build parameter table and return type nodes."""
        arg = self.arguments[0]
        func_name = arg.split(".")[-1] if "." in arg else arg

        tools: dict[str, ToolInfo] = getattr(self.env, "fastmcp_tools", {})
        tool = tools.get(func_name)

        if tool is None:
            return [
                self.state.document.reporter.warning(
                    f"fastmcp-tool-input: tool '{func_name}' not found.",
                    line=self.lineno,
                )
            ]

        result: list[nodes.Node] = []

        # Parameter table
        if tool.params:
            result.append(_make_para(nodes.strong("", "Parameters")))
            headers = ["Parameter", "Type", "Required", "Default", "Description"]
            rows: list[list[str | nodes.Node]] = []
            for p in tool.params:
                # Build description node — parse RST inline markup
                desc_node = self._build_description(p)

                # Type cell — detect enum-only types and simplify
                type_cell, is_enum = _make_type_cell_smart(p.type_str)

                # If enum, append allowed values to description
                if is_enum and p.type_str:
                    enum_values = _extract_enum_values(p.type_str)
                    if enum_values:
                        desc_node += nodes.Text(" One of: ")
                        for i, val in enumerate(enum_values):
                            if i > 0:
                                desc_node += nodes.Text(", ")
                            desc_node += nodes.literal("", val)
                        desc_node += nodes.Text(".")

                # Default — suppress "None" as visual noise
                default_cell: str | nodes.Node = "—"
                if p.default and p.default != "None":
                    default_cell = _make_para(_make_literal(p.default))

                rows.append(
                    [
                        _make_para(_make_literal(p.name)),
                        type_cell,
                        "yes" if p.required else "no",
                        default_cell,
                        desc_node,
                    ]
                )
            result.append(
                _make_table(headers, rows, col_widths=[15, 15, 8, 10, 52]),
            )

        return result

    def _build_description(self, p: ParamInfo) -> nodes.paragraph:
        """Build a description paragraph, parsing RST inline markup."""
        if p.description:
            return _parse_rst_inline(
                p.description,
                self.state,
                self.lineno,
            )
        return nodes.paragraph("", "—")


class FastMCPToolSummaryDirective(SphinxDirective):
    """Generate a summary table of all tools grouped by safety tier.

    Produces three tables (Inspect, Act, Destroy) with tool names
    linked to their sections on area pages.

    Usage::

        ```{fastmcp-toolsummary}
        ```
    """

    required_arguments = 0
    optional_arguments = 0
    has_content = False

    def run(self) -> list[nodes.Node]:
        """Build grouped summary tables."""
        tools: dict[str, ToolInfo] = getattr(self.env, "fastmcp_tools", {})

        if not tools:
            return [
                self.state.document.reporter.warning(
                    "fastmcp-toolsummary: no tools found.",
                    line=self.lineno,
                )
            ]

        groups: dict[str, list[ToolInfo]] = {
            "readonly": [],
            "mutating": [],
            "destructive": [],
        }
        for tool in tools.values():
            groups.setdefault(tool.safety, []).append(tool)

        result_nodes: list[nodes.Node] = []

        tier_order = [
            ("readonly", "Inspect", "Read tmux state without changing anything."),
            ("mutating", "Act", "Create or modify tmux objects."),
            ("destructive", "Destroy", "Tear down tmux objects. Not reversible."),
        ]

        for safety, label, desc in tier_order:
            tier_tools = groups.get(safety, [])
            if not tier_tools:
                continue

            # Section for this tier
            section = nodes.section()
            section["ids"].append(label.lower())
            self.state.document.note_explicit_target(section)
            section += nodes.title("", label)
            section += nodes.paragraph("", desc)

            # Summary table
            headers = ["Tool", "Description"]
            rows: list[list[str | nodes.Node]] = []
            for tool in sorted(tier_tools, key=lambda t: t.name):
                first_line = _first_paragraph(tool.docstring)
                # Link to the tool's section on its area page
                ref = nodes.reference("", "", internal=True)
                ref["refuri"] = f"{tool.area}/#{tool.name.replace('_', '-')}"
                ref += nodes.literal("", tool.name)
                rows.append(
                    [
                        _make_para(ref),
                        _parse_rst_inline(first_line, self.state, self.lineno),
                    ]
                )
            section += _make_table(headers, rows, col_widths=[30, 70])

            result_nodes.append(section)

        return result_nodes


# ---------------------------------------------------------------------------
# Extension setup
# ---------------------------------------------------------------------------


def _register_tool_labels(app: Sphinx, doctree: nodes.document) -> None:
    """Register tool sections with StandardDomain for site-wide {ref} links.

    ``note_explicit_target()`` only registers with docutils, not with Sphinx's
    StandardDomain.  This hook mirrors the pattern used by
    ``sphinx.ext.autosectionlabel`` so that ``{ref}`list-sessions``` works
    from any page.
    """
    domain = t.cast("StandardDomain", app.env.get_domain("std"))
    docname = app.env.docname
    for section in doctree.findall(nodes.section):
        if not section["ids"]:
            continue
        section_id = section["ids"][0]
        if section.children and isinstance(section[0], nodes.title):
            title = section[0].astext()
            domain.anonlabels[section_id] = (docname, section_id)
            domain.labels[section_id] = (docname, section_id, title)


def setup(app: Sphinx) -> ExtensionMetadata:
    """Register the fastmcp_autodoc extension."""
    app.connect("builder-inited", _collect_tools)
    app.connect("doctree-read", _register_tool_labels)
    app.add_directive("fastmcp-tool", FastMCPToolDirective)
    app.add_directive("fastmcp-tool-input", FastMCPToolInputDirective)
    app.add_directive("fastmcp-toolsummary", FastMCPToolSummaryDirective)

    return {
        "version": "0.1.0",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
