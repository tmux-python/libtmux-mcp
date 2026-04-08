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
import logging
import re
import typing as t
from dataclasses import dataclass

from docutils import nodes
from sphinx import addnodes
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective

if t.TYPE_CHECKING:
    from sphinx.domains.std import StandardDomain
    from sphinx.util.typing import ExtensionMetadata

logger = logging.getLogger(__name__)

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

SECTION_BADGE_MAP: dict[str, str] = {
    "Inspect": "readonly",
    "Act": "mutating",
    "Destroy": "destructive",
}

TAG_READONLY = "readonly"
TAG_MUTATING = "mutating"
TAG_DESTRUCTIVE = "destructive"

_MODEL_MODULE = "libtmux_mcp.models"
_model_classes_cache: set[str] | None = None


def _discover_model_classes() -> set[str]:
    """Discover all BaseModel subclasses in libtmux_mcp.models.

    Results are cached after first call. Only discovers classes whose
    ``__module__`` matches ``_MODEL_MODULE`` to prevent third-party leakage.
    """
    global _model_classes_cache
    if _model_classes_cache is not None:
        return _model_classes_cache
    import inspect as _inspect

    from pydantic import BaseModel

    try:
        mod = importlib.import_module(_MODEL_MODULE)
    except ImportError:
        logger.warning("fastmcp_autodoc: could not import %s", _MODEL_MODULE)
        _model_classes_cache = set()
        return _model_classes_cache
    _model_classes_cache = {
        name
        for name, obj in _inspect.getmembers(mod, _inspect.isclass)
        if issubclass(obj, BaseModel)
        and getattr(obj, "__module__", "") == _MODEL_MODULE
    }
    return _model_classes_cache


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


@dataclass
class ResourceInfo:
    """Collected metadata for a single MCP resource."""

    name: str
    qualified_name: str
    title: str
    uri_template: str
    docstring: str
    params: list[ParamInfo]
    return_annotation: str


@dataclass
class ModelFieldInfo:
    """Extracted field information for a Pydantic model."""

    name: str
    type_str: str
    required: bool
    default: str
    description: str


@dataclass
class ModelInfo:
    """Collected metadata for a single Pydantic model."""

    name: str
    qualified_name: str
    docstring: str
    fields: list[ModelFieldInfo]


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


def _single_type_xref(name: str) -> addnodes.pending_xref:
    """Create a ``pending_xref`` for a single type name.

    Known model classes are qualified to ``libtmux_mcp.models.X``.
    Builtins (``str``, ``list``, ``int``, etc.) target the Python domain.
    """
    target = f"{_MODEL_MODULE}.{name}" if name in _discover_model_classes() else name
    return addnodes.pending_xref(
        "",
        nodes.literal("", name),
        refdomain="py",
        reftype="class",
        reftarget=target,
    )


def _make_type_xref(type_str: str) -> nodes.paragraph:
    """Render a return type annotation with cross-reference links.

    Handles ``list[X]`` generics and bare type names.
    Each type component becomes a ``pending_xref`` that Sphinx resolves
    into a hyperlink (internal or intersphinx).
    """
    para = nodes.paragraph("")
    m = re.match(r"^(list|set|tuple)\[(.+)\]$", type_str)
    if m:
        container, inner = m.group(1), m.group(2)
        para += _single_type_xref(container)
        para += nodes.Text("[")
        para += _single_type_xref(inner)
        para += nodes.Text("]")
    else:
        para += _single_type_xref(type_str)
    return para


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


class _safety_badge_node(nodes.General, nodes.Inline, nodes.Element):  # type: ignore[misc]
    """Custom node for safety badges with ARIA attributes in HTML output."""


def _visit_safety_badge_html(self: t.Any, node: _safety_badge_node) -> None:
    """Emit opening ``<span>`` with classes, role, and aria-label."""
    classes = " ".join(node.get("classes", []))
    safety = node.get("safety", "")
    self.body.append(
        f'<span class="{classes}" role="note" aria-label="Safety tier: {safety}">'
    )


def _depart_safety_badge_html(self: t.Any, node: _safety_badge_node) -> None:
    """Close the ``<span>``."""
    self.body.append("</span>")


def _safety_badge(safety: str) -> _safety_badge_node:
    """Create a colored safety badge node with ARIA attributes."""
    _base = ["sd-sphinx-override", "sd-badge"]
    classes = {
        "readonly": [*_base, "sd-bg-success", "sd-bg-text-success"],
        "mutating": [*_base, "sd-bg-warning", "sd-bg-text-warning"],
        "destructive": [*_base, "sd-bg-danger", "sd-bg-text-danger"],
    }
    badge = _safety_badge_node(
        "",
        nodes.Text(safety),
        classes=classes.get(safety, []),
        safety=safety,
    )
    return badge


class _resource_badge_node(nodes.General, nodes.Inline, nodes.Element):  # type: ignore[misc]
    """Custom node for resource badges with ARIA attributes in HTML output."""


def _visit_resource_badge_html(self: t.Any, node: _resource_badge_node) -> None:
    """Emit opening ``<span>`` with classes, role, and aria-label."""
    classes = " ".join(node.get("classes", []))
    self.body.append(
        f'<span class="{classes}" role="note" aria-label="Type: resource">'
    )


def _depart_resource_badge_html(self: t.Any, node: _resource_badge_node) -> None:
    """Close the ``<span>``."""
    self.body.append("</span>")


def _resource_badge() -> _resource_badge_node:
    """Create a blue resource badge node with ARIA attributes."""
    _base = ["sd-sphinx-override", "sd-badge"]
    badge = _resource_badge_node(
        "",
        nodes.Text("resource"),
        classes=[*_base, "sd-bg-info", "sd-bg-text-info"],
    )
    return badge


class _model_badge_node(nodes.General, nodes.Inline, nodes.Element):  # type: ignore[misc]
    """Custom node for model badges with ARIA attributes in HTML output."""


def _visit_model_badge_html(self: t.Any, node: _model_badge_node) -> None:
    """Emit opening ``<span>`` with classes, role, and aria-label."""
    classes = " ".join(node.get("classes", []))
    self.body.append(f'<span class="{classes}" role="note" aria-label="Type: model">')


def _depart_model_badge_html(self: t.Any, node: _model_badge_node) -> None:
    """Close the ``<span>``."""
    self.body.append("</span>")


def _model_badge() -> _model_badge_node:
    """Create a purple model badge node with ARIA attributes."""
    _base = ["sd-sphinx-override", "sd-badge"]
    badge = _model_badge_node(
        "",
        nodes.Text("model"),
        classes=[*_base, "sd-bg-primary", "sd-bg-text-primary"],
    )
    return badge


class _resource_ref_placeholder(nodes.General, nodes.Inline, nodes.Element):  # type: ignore[misc]
    """Placeholder node for ``{resource}`` and ``{resourceref}`` roles.

    Resolved at ``doctree-resolved`` by ``_resolve_resource_refs``.
    The ``show_badge`` attribute controls whether the resource badge is appended.
    """


class _model_ref_placeholder(nodes.General, nodes.Inline, nodes.Element):  # type: ignore[misc]
    """Placeholder node for ``{model}`` and ``{modelref}`` roles.

    Resolved at ``doctree-resolved`` by ``_resolve_model_refs``.
    The ``show_badge`` attribute controls whether the model badge is appended.
    """


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


class _ResourceCollector:
    """Mock FastMCP that captures resource registrations."""

    def __init__(self) -> None:
        self.resources: list[ResourceInfo] = []
        self._current_module: str = ""

    def resource(
        self,
        uri_template: str,
        title: str = "",
        **kwargs: t.Any,
    ) -> t.Callable[[t.Callable[..., t.Any]], t.Callable[..., t.Any]]:
        def decorator(func: t.Callable[..., t.Any]) -> t.Callable[..., t.Any]:
            self.resources.append(
                ResourceInfo(
                    name=func.__name__,
                    qualified_name=f"{self._current_module}.{func.__name__}",
                    title=title or func.__name__.replace("_", " ").title(),
                    uri_template=uri_template,
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
            logger.warning(
                "fastmcp_autodoc: failed to load tool module %s",
                mod_name,
                exc_info=True,
            )

    app.env.fastmcp_tools = {tool.name: tool for tool in collector.tools}  # type: ignore[attr-defined]


def _collect_resources(app: Sphinx) -> None:
    """Collect resource metadata from libtmux_mcp source at build time."""
    collector = _ResourceCollector()

    resource_modules = ["hierarchy"]

    for mod_name in resource_modules:
        collector._current_module = mod_name
        try:
            mod = importlib.import_module(f"libtmux_mcp.resources.{mod_name}")
            if hasattr(mod, "register"):
                mod.register(collector)
        except Exception:
            logger.warning(
                "fastmcp_autodoc: failed to load resource module %s",
                mod_name,
                exc_info=True,
            )

    app.env.fastmcp_resources = {r.name: r for r in collector.resources}  # type: ignore[attr-defined]


def _collect_models(app: Sphinx) -> None:
    """Collect Pydantic model metadata from libtmux_mcp.models at build time."""
    from pydantic import BaseModel

    try:
        mod = importlib.import_module(_MODEL_MODULE)
    except ImportError:
        logger.warning("fastmcp_autodoc: could not import %s", _MODEL_MODULE)
        app.env.fastmcp_models = {}  # type: ignore[attr-defined]
        return

    models: dict[str, ModelInfo] = {}
    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if not issubclass(obj, BaseModel):
            continue
        if getattr(obj, "__module__", "") != _MODEL_MODULE:
            continue

        fields: list[ModelFieldInfo] = []
        for field_name, field_info in obj.model_fields.items():
            # Determine type string
            ann = obj.__annotations__.get(field_name, "")
            type_str = _format_annotation(ann)

            # Determine required / default
            has_default_factory = (
                hasattr(field_info, "default_factory")
                and field_info.default_factory is not None
            )
            has_default = not field_info.is_required() and not has_default_factory

            if has_default_factory:
                required = False
                factory = field_info.default_factory
                # Show factory name for common factories
                default_str = f"{factory.__name__}()" if factory else ""
            elif has_default:
                required = False
                default_val = field_info.default
                if default_val is None:
                    default_str = "None"
                elif isinstance(default_val, bool):
                    default_str = str(default_val)
                elif isinstance(default_val, str):
                    default_str = repr(default_val)
                else:
                    default_str = str(default_val)
            else:
                required = True
                default_str = ""

            # Extract description from Field(description=...)
            description = ""
            if hasattr(field_info, "description") and field_info.description:
                description = field_info.description

            fields.append(
                ModelFieldInfo(
                    name=field_name,
                    type_str=type_str,
                    required=required,
                    default=default_str,
                    description=description,
                )
            )

        models[name] = ModelInfo(
            name=name,
            qualified_name=f"{_MODEL_MODULE}.{name}",
            docstring=obj.__doc__ or "",
            fields=fields,
        )

    app.env.fastmcp_models = models  # type: ignore[attr-defined]


def _collect_all(app: Sphinx) -> None:
    """Collect tools, resources, and models at build time."""
    _collect_tools(app)
    _collect_resources(app)
    _collect_models(app)


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
            returns_para = nodes.paragraph("")
            returns_para += nodes.strong("", "Returns: ")
            type_para = _make_type_xref(tool.return_annotation)
            for child in type_para.children:
                returns_para += child.deepcopy()
            section += returns_para

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


class FastMCPResourceDirective(SphinxDirective):
    """Autodocument a single MCP resource as a proper section.

    Creates a section node (visible in ToC) containing:
    - Resource badge + one-line description
    - URI template literal block
    - Optional parameter table
    - Return type

    Usage::

        ```{fastmcp-resource} hierarchy.get_sessions
        ```
    """

    required_arguments = 1
    optional_arguments = 0
    has_content = True
    final_argument_whitespace = False

    def run(self) -> list[nodes.Node]:
        """Build resource section nodes."""
        arg = self.arguments[0]
        func_name = arg.split(".")[-1] if "." in arg else arg

        resources: dict[str, ResourceInfo] = getattr(self.env, "fastmcp_resources", {})
        resource = resources.get(func_name)

        if resource is None:
            return [
                self.state.document.reporter.warning(
                    f"fastmcp-resource: resource '{func_name}' not found. "
                    f"Available: {', '.join(sorted(resources.keys()))}",
                    line=self.lineno,
                )
            ]

        return self._build_resource_section(resource)

    def _build_resource_section(self, resource: ResourceInfo) -> list[nodes.Node]:
        """Build section: title, badge, description, URI template, params."""
        document = self.state.document

        # Section with anchor ID
        section_id = f"resource-{resource.name.replace('_', '-')}"
        section = nodes.section()
        section["ids"].append(section_id)
        document.note_explicit_target(section)

        # Title: resource name + resource badge
        title_node = nodes.title("", "")
        title_node += nodes.literal("", resource.name)
        title_node += nodes.Text(" ")
        title_node += _resource_badge()
        section += title_node

        # Description paragraph
        first_para = _first_paragraph(resource.docstring)
        if first_para:
            desc_para = _parse_rst_inline(first_para, self.state, self.lineno)
            section += desc_para

        # URI template as literal block
        uri_block = nodes.literal_block("", resource.uri_template)
        uri_block["language"] = "none"
        uri_block["classes"].append("fastmcp-uri-template")
        section += uri_block

        # Returns
        if resource.return_annotation:
            returns_para = nodes.paragraph("")
            returns_para += nodes.strong("", "Returns: ")
            type_para = _make_type_xref(resource.return_annotation)
            for child in type_para.children:
                returns_para += child.deepcopy()
            section += returns_para

        # Parameter table
        if resource.params:
            section += _make_para(nodes.strong("", "Parameters"))
            headers = ["Parameter", "Type", "Required", "Default", "Description"]
            rows: list[list[str | nodes.Node]] = []
            for p in resource.params:
                desc_node = (
                    _parse_rst_inline(p.description, self.state, self.lineno)
                    if p.description
                    else nodes.paragraph("", "\u2014")
                )

                type_cell, _is_enum = _make_type_cell_smart(p.type_str)

                default_cell: str | nodes.Node = "\u2014"
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
            section += _make_table(headers, rows, col_widths=[15, 15, 8, 10, 52])

        return [section]


class FastMCPResourceSummaryDirective(SphinxDirective):
    """Generate a summary table of all resources.

    Produces a single table with URI Template, Title, and Description columns.

    Usage::

        ```{fastmcp-resourcesummary}
        ```
    """

    required_arguments = 0
    optional_arguments = 0
    has_content = False

    def run(self) -> list[nodes.Node]:
        """Build resource summary table."""
        resources: dict[str, ResourceInfo] = getattr(self.env, "fastmcp_resources", {})

        if not resources:
            return [
                self.state.document.reporter.warning(
                    "fastmcp-resourcesummary: no resources found.",
                    line=self.lineno,
                )
            ]

        headers = ["URI Template", "Title", "Description"]
        rows: list[list[str | nodes.Node]] = []
        for resource in sorted(resources.values(), key=lambda r: r.uri_template):
            first_line = _first_paragraph(resource.docstring)
            ref = nodes.reference("", "", internal=True)
            section_id = f"resource-{resource.name.replace('_', '-')}"
            ref["refuri"] = f"#{section_id}"
            ref += nodes.literal("", resource.uri_template)
            rows.append(
                [
                    _make_para(ref),
                    resource.title,
                    _parse_rst_inline(first_line, self.state, self.lineno),
                ]
            )

        return [_make_table(headers, rows, col_widths=[35, 15, 50])]


class FastMCPModelDirective(SphinxDirective):
    """Autodocument a single Pydantic model as a proper section.

    Creates a section node (visible in ToC) containing:
    - Model badge + docstring
    - Field table (Field, Type, Required, Default, Description)

    Options:
    - ``:fields:`` — comma-separated allowlist of fields to include
    - ``:exclude:`` — comma-separated denylist of fields to exclude

    Usage::

        ```{fastmcp-model} SessionInfo
        ```
    """

    required_arguments = 1
    optional_arguments = 0
    has_content = True
    final_argument_whitespace = False
    option_spec: t.ClassVar[dict[str, t.Any]] = {
        "fields": lambda x: x,
        "exclude": lambda x: x,
    }

    def run(self) -> list[nodes.Node]:
        """Build model section nodes."""
        model_name = self.arguments[0].strip()

        models: dict[str, ModelInfo] = getattr(self.env, "fastmcp_models", {})
        model = models.get(model_name)

        if model is None:
            return [
                self.state.document.reporter.warning(
                    f"fastmcp-model: model '{model_name}' not found. "
                    f"Available: {', '.join(sorted(models.keys()))}",
                    line=self.lineno,
                )
            ]

        return self._build_model_section(model)

    def _build_model_section(self, model: ModelInfo) -> list[nodes.Node]:
        """Build section: title, badge, docstring, field table."""
        document = self.state.document

        # Section with anchor ID
        section_id = f"model-{model.name}"
        section = nodes.section()
        section["ids"].append(section_id)
        document.note_explicit_target(section)

        # Title: model name + model badge
        title_node = nodes.title("", "")
        title_node += nodes.literal("", model.name)
        title_node += nodes.Text(" ")
        title_node += _model_badge()
        section += title_node

        # Docstring
        first_para = _first_paragraph(model.docstring)
        if first_para:
            desc_para = _parse_rst_inline(first_para, self.state, self.lineno)
            section += desc_para

        # Field table
        fields = self._filter_fields(model.fields)
        if fields:
            section += self._build_field_table(fields)

        return [section]

    def _filter_fields(self, fields: list[ModelFieldInfo]) -> list[ModelFieldInfo]:
        """Apply :fields: and :exclude: options."""
        result = list(fields)
        fields_opt = self.options.get("fields")
        if fields_opt:
            allow = {f.strip() for f in fields_opt.split(",")}
            result = [f for f in result if f.name in allow]
        exclude_opt = self.options.get("exclude")
        if exclude_opt:
            deny = {f.strip() for f in exclude_opt.split(",")}
            result = [f for f in result if f.name not in deny]
        return result

    def _build_field_table(self, fields: list[ModelFieldInfo]) -> nodes.table:
        """Build a field table."""
        headers = ["Field", "Type", "Required", "Default", "Description"]
        rows: list[list[str | nodes.Node]] = []
        for f in fields:
            type_cell, _is_enum = _make_type_cell_smart(f.type_str)

            default_cell: str | nodes.Node = "\u2014"
            if f.default and f.default != "None":
                default_cell = _make_para(_make_literal(f.default))

            desc = f.description or "\u2014"

            rows.append(
                [
                    _make_para(_make_literal(f.name)),
                    type_cell,
                    "yes" if f.required else "no",
                    default_cell,
                    desc,
                ]
            )
        return _make_table(headers, rows, col_widths=[15, 15, 8, 10, 52])


class FastMCPModelFieldsDirective(SphinxDirective):
    """Emit the field table for a model without a section wrapper.

    Useful for embedding model fields inline in other content.

    Options:
    - ``:fields:`` — comma-separated allowlist of fields to include
    - ``:exclude:`` — comma-separated denylist of fields to exclude
    - ``:link-header:`` — if set, adds a header linking to the model section

    Usage::

        ```{fastmcp-model-fields} SessionInfo
        ```
    """

    required_arguments = 1
    optional_arguments = 0
    has_content = False
    option_spec: t.ClassVar[dict[str, t.Any]] = {
        "fields": lambda x: x,
        "exclude": lambda x: x,
        "link-header": lambda x: x,
    }

    def run(self) -> list[nodes.Node]:
        """Build field table nodes."""
        model_name = self.arguments[0].strip()

        models: dict[str, ModelInfo] = getattr(self.env, "fastmcp_models", {})
        model = models.get(model_name)

        if model is None:
            return [
                self.state.document.reporter.warning(
                    f"fastmcp-model-fields: model '{model_name}' not found.",
                    line=self.lineno,
                )
            ]

        result: list[nodes.Node] = []

        # Optional link header
        link_header = self.options.get("link-header")
        if link_header is not None:
            ref = nodes.reference("", "", internal=True)
            section_id = f"model-{model.name}"
            ref["refuri"] = f"#{section_id}"
            ref += nodes.literal("", model.name)
            result.append(_make_para(ref))

        # Filter and build table
        fields = self._filter_fields(model.fields)
        if fields:
            headers = ["Field", "Type", "Required", "Default", "Description"]
            rows: list[list[str | nodes.Node]] = []
            for f in fields:
                type_cell, _is_enum = _make_type_cell_smart(f.type_str)

                default_cell: str | nodes.Node = "\u2014"
                if f.default and f.default != "None":
                    default_cell = _make_para(_make_literal(f.default))

                desc = f.description or "\u2014"

                rows.append(
                    [
                        _make_para(_make_literal(f.name)),
                        type_cell,
                        "yes" if f.required else "no",
                        default_cell,
                        desc,
                    ]
                )
            result.append(_make_table(headers, rows, col_widths=[15, 15, 8, 10, 52]))

        return result

    def _filter_fields(self, fields: list[ModelFieldInfo]) -> list[ModelFieldInfo]:
        """Apply :fields: and :exclude: options."""
        result = list(fields)
        fields_opt = self.options.get("fields")
        if fields_opt:
            allow = {f.strip() for f in fields_opt.split(",")}
            result = [f for f in result if f.name in allow]
        exclude_opt = self.options.get("exclude")
        if exclude_opt:
            deny = {f.strip() for f in exclude_opt.split(",")}
            result = [f for f in result if f.name not in deny]
        return result


class FastMCPModelSummaryDirective(SphinxDirective):
    """Generate a summary table of all models.

    Produces a single table with Model and Description columns.

    Usage::

        ```{fastmcp-modelsummary}
        ```
    """

    required_arguments = 0
    optional_arguments = 0
    has_content = False

    def run(self) -> list[nodes.Node]:
        """Build model summary table."""
        models: dict[str, ModelInfo] = getattr(self.env, "fastmcp_models", {})

        if not models:
            return [
                self.state.document.reporter.warning(
                    "fastmcp-modelsummary: no models found.",
                    line=self.lineno,
                )
            ]

        headers = ["Model", "Description"]
        rows: list[list[str | nodes.Node]] = []
        for model in sorted(models.values(), key=lambda m: m.name):
            first_line = _first_paragraph(model.docstring)
            ref = nodes.reference("", "", internal=True)
            section_id = f"model-{model.name}"
            ref["refuri"] = f"#{section_id}"
            ref += nodes.literal("", model.name)
            rows.append(
                [
                    _make_para(ref),
                    _parse_rst_inline(first_line, self.state, self.lineno),
                ]
            )

        return [_make_table(headers, rows, col_widths=[30, 70])]


# ---------------------------------------------------------------------------
# Extension setup
# ---------------------------------------------------------------------------


def _register_tool_labels(app: Sphinx, doctree: nodes.document) -> None:
    """Register tool sections with StandardDomain for site-wide {ref} links.

    ``note_explicit_target()`` only registers with docutils, not with Sphinx's
    StandardDomain.  This hook mirrors the pattern used by
    ``sphinx.ext.autosectionlabel`` so that ``{ref}`list-sessions``` works
    from any page.

    The primary label uses just the tool name (no safety badge) so that
    ``{ref}`` renders a clean ``tool_name`` link.  Use ``{tool}`` role
    for a link that includes the colored safety badge.
    """
    domain = t.cast("StandardDomain", app.env.get_domain("std"))
    docname = app.env.docname
    for section in doctree.findall(nodes.section):
        if not section["ids"]:
            continue
        section_id = section["ids"][0]
        if section.children and isinstance(section[0], nodes.title):
            # Only register sections whose title starts with a literal
            # (tool sections generated by fastmcp-tool have nodes.literal
            # as the first title child).  Non-tool sections (e.g. "Inspect",
            # "Act") don't need site-wide labels.
            title_node = section[0]
            tool_name = ""
            for child in title_node.children:
                if isinstance(child, nodes.literal):
                    tool_name = child.astext()
                    break
            if not tool_name:
                continue
            domain.anonlabels[section_id] = (docname, section_id)
            domain.labels[section_id] = (docname, section_id, tool_name)


_SECTION_BADGE_PAGES: set[str] = {"tools/index", "index"}


def _add_section_badges(
    app: Sphinx,
    doctree: nodes.document,
    fromdocname: str,
) -> None:
    """Replace parenthesized tier names with colored badges in headings.

    Matches both bare headings (``Inspect``) and parenthesized variants
    (``Inspect (readonly)``).  The parenthesized text is stripped and
    replaced with a badge node.

    Only applied to pages in ``_SECTION_BADGE_PAGES`` — individual tool
    pages already have per-tool badges, making section-level badges
    redundant.

    Runs at ``doctree-resolved`` — section IDs are already frozen, so
    modifying the title doesn't affect anchors or cross-refs.
    """
    if fromdocname not in _SECTION_BADGE_PAGES:
        return
    for section in doctree.findall(nodes.section):
        if not section.children or not isinstance(section[0], nodes.title):
            continue
        title_text = section[0].astext().strip()

        # Try exact match first ("Inspect")
        safety = SECTION_BADGE_MAP.get(title_text)
        if safety is not None:
            section[0] += nodes.Text(" ")
            section[0] += _safety_badge(safety)
            continue

        # Try parenthesized match ("Inspect (readonly)")
        m = re.match(r"^(\w+)\s*\((\w+)\)$", title_text)
        if m:
            heading, tier = m.group(1), m.group(2)
            if heading in SECTION_BADGE_MAP and tier == SECTION_BADGE_MAP[heading]:
                # Replace title children: strip parenthesized text, add badge
                title_node = section[0]
                title_node.clear()
                title_node += nodes.Text(heading + " ")
                title_node += _safety_badge(tier)


class _tool_ref_placeholder(nodes.General, nodes.Inline, nodes.Element):  # type: ignore[misc]
    """Placeholder node for ``{tool}`` and ``{toolref}`` roles.

    Resolved at ``doctree-resolved`` by ``_resolve_tool_refs``.
    The ``show_badge`` attribute controls whether the safety badge is appended.
    """


def _resolve_tool_refs(
    app: Sphinx,
    doctree: nodes.document,
    fromdocname: str,
) -> None:
    """Resolve ``{tool}``, ``{toolref}``, and ``{toolicon*}`` placeholders.

    ``{tool}`` renders as ``code`` + safety badge (text + icon).
    ``{toolref}`` renders as ``code`` only (no badge).
    ``{toolicon}``/``{tooliconl}`` — icon-only badge left of code.
    ``{tooliconr}`` — icon-only badge right of code.
    ``{tooliconil}`` — icon-only badge inside code, left of text.
    ``{tooliconir}`` — icon-only badge inside code, right of text.

    Runs at ``doctree-resolved`` — after all labels are registered and
    standard ``{ref}`` resolution is done.
    """
    domain = t.cast("StandardDomain", app.env.get_domain("std"))
    builder = app.builder
    tool_data: dict[str, ToolInfo] = getattr(app.env, "fastmcp_tools", {})

    for node in list(doctree.findall(_tool_ref_placeholder)):
        target = node.get("reftarget", "")
        show_badge = node.get("show_badge", True)
        icon_pos = node.get("icon_pos", "")
        label_info = domain.labels.get(target)
        if label_info is None:
            node.replace_self(nodes.literal("", target.replace("-", "_")))
            continue

        todocname, labelid, _title = label_info
        tool_name = target.replace("-", "_")

        newnode = nodes.reference("", "", internal=True)
        try:
            newnode["refuri"] = builder.get_relative_uri(fromdocname, todocname)
            if labelid:
                newnode["refuri"] += "#" + labelid
        except Exception:
            logger.warning(
                "fastmcp_autodoc: failed to resolve URI for %s -> %s",
                fromdocname,
                todocname,
            )
            newnode["refuri"] = "#" + labelid
        newnode["classes"].append("reference")
        newnode["classes"].append("internal")

        if icon_pos:
            tool_info = tool_data.get(tool_name)
            badge = None
            if tool_info:
                badge = _safety_badge(tool_info.safety)
                badge["classes"].append("icon-only")
                if icon_pos.startswith("inline"):
                    badge["classes"].append("icon-only-inline")
                badge.children.clear()
                badge += nodes.Text("")

            if icon_pos == "left":
                if badge:
                    newnode += badge
                newnode += nodes.literal("", tool_name)
            elif icon_pos == "right":
                newnode += nodes.literal("", tool_name)
                if badge:
                    newnode += badge
            elif icon_pos == "inline-left":
                code_node = nodes.literal("", "")
                if badge:
                    code_node += badge
                code_node += nodes.Text(tool_name)
                newnode += code_node
            elif icon_pos == "inline-right":
                code_node = nodes.literal("", "")
                code_node += nodes.Text(tool_name)
                if badge:
                    code_node += badge
                newnode += code_node
        else:
            newnode += nodes.literal("", tool_name)
            if show_badge:
                tool_info = tool_data.get(tool_name)
                if tool_info:
                    newnode += nodes.Text(" ")
                    newnode += _safety_badge(tool_info.safety)

        node.replace_self(newnode)


def _tool_role(
    name: str,
    rawtext: str,
    text: str,
    lineno: int,
    inliner: object,
    options: dict[str, object] | None = None,
    content: list[str] | None = None,
) -> tuple[list[nodes.Node], list[nodes.system_message]]:
    """Inline role ``:tool:`capture-pane``` → linked tool name + safety badge.

    Creates a placeholder node resolved later by ``_resolve_tool_refs``.
    """
    target = text.strip().replace("_", "-")
    node = _tool_ref_placeholder(rawtext, reftarget=target, show_badge=True)
    return [node], []


def _toolref_role(
    name: str,
    rawtext: str,
    text: str,
    lineno: int,
    inliner: object,
    options: dict[str, object] | None = None,
    content: list[str] | None = None,
) -> tuple[list[nodes.Node], list[nodes.system_message]]:
    """Inline role ``:toolref:`capture-pane``` → code-linked tool name, no badge.

    Like ``{tool}`` but without the safety badge. Use in dense contexts
    (tables, inline prose) where badges would be too heavy.
    """
    target = text.strip().replace("_", "-")
    node = _tool_ref_placeholder(rawtext, reftarget=target, show_badge=False)
    return [node], []


def _make_toolicon_role(
    icon_pos: str,
) -> t.Callable[..., tuple[list[nodes.Node], list[nodes.system_message]]]:
    """Create an icon-only tool reference role for a given position."""

    def role_fn(
        name: str,
        rawtext: str,
        text: str,
        lineno: int,
        inliner: object,
        options: dict[str, object] | None = None,
        content: list[str] | None = None,
    ) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        target = text.strip().replace("_", "-")
        node = _tool_ref_placeholder(
            rawtext,
            reftarget=target,
            show_badge=False,
            icon_pos=icon_pos,
        )
        return [node], []

    return role_fn


# {toolicon} is a convenience alias for {tooliconl} (both render icon-left)
_toolicon_role = _make_toolicon_role("left")
_tooliconl_role = _make_toolicon_role("left")
_tooliconr_role = _make_toolicon_role("right")
_tooliconil_role = _make_toolicon_role("inline-left")
_tooliconir_role = _make_toolicon_role("inline-right")


def _badge_role(
    name: str,
    rawtext: str,
    text: str,
    lineno: int,
    inliner: object,
    options: dict[str, object] | None = None,
    content: list[str] | None = None,
) -> tuple[list[nodes.Node], list[nodes.system_message]]:
    """Inline role ``:badge:`readonly``` → colored safety badge span."""
    return [_safety_badge(text.strip())], []


def _resource_role(
    name: str,
    rawtext: str,
    text: str,
    lineno: int,
    inliner: object,
    options: dict[str, object] | None = None,
    content: list[str] | None = None,
) -> tuple[list[nodes.Node], list[nodes.system_message]]:
    """Inline role ``:resource:`get-sessions``` → linked name + resource badge."""
    target = text.strip().replace("_", "-")
    node = _resource_ref_placeholder(rawtext, reftarget=target, show_badge=True)
    return [node], []


def _resourceref_role(
    name: str,
    rawtext: str,
    text: str,
    lineno: int,
    inliner: object,
    options: dict[str, object] | None = None,
    content: list[str] | None = None,
) -> tuple[list[nodes.Node], list[nodes.system_message]]:
    """Inline role ``:resourceref:`get-sessions``` → code-linked, no badge."""
    target = text.strip().replace("_", "-")
    node = _resource_ref_placeholder(rawtext, reftarget=target, show_badge=False)
    return [node], []


def _model_role(
    name: str,
    rawtext: str,
    text: str,
    lineno: int,
    inliner: object,
    options: dict[str, object] | None = None,
    content: list[str] | None = None,
) -> tuple[list[nodes.Node], list[nodes.system_message]]:
    """Inline role ``:model:`SessionInfo``` → linked name + model badge."""
    target = text.strip()
    node = _model_ref_placeholder(rawtext, reftarget=target, show_badge=True)
    return [node], []


def _modelref_role(
    name: str,
    rawtext: str,
    text: str,
    lineno: int,
    inliner: object,
    options: dict[str, object] | None = None,
    content: list[str] | None = None,
) -> tuple[list[nodes.Node], list[nodes.system_message]]:
    """Inline role ``:modelref:`SessionInfo``` → code-linked, no badge."""
    target = text.strip()
    node = _model_ref_placeholder(rawtext, reftarget=target, show_badge=False)
    return [node], []


def _register_resource_labels(app: Sphinx, doctree: nodes.document) -> None:
    """Register resource sections with StandardDomain for site-wide {ref} links.

    Same pattern as ``_register_tool_labels`` but for sections with
    ``resource-`` prefixed IDs.
    """
    domain = t.cast("StandardDomain", app.env.get_domain("std"))
    docname = app.env.docname
    for section in doctree.findall(nodes.section):
        if not section["ids"]:
            continue
        section_id = section["ids"][0]
        if not section_id.startswith("resource-"):
            continue
        if section.children and isinstance(section[0], nodes.title):
            title_node = section[0]
            resource_name = ""
            for child in title_node.children:
                if isinstance(child, nodes.literal):
                    resource_name = child.astext()
                    break
            if not resource_name:
                continue
            domain.anonlabels[section_id] = (docname, section_id)
            domain.labels[section_id] = (docname, section_id, resource_name)


def _register_model_labels(app: Sphinx, doctree: nodes.document) -> None:
    """Register model sections with StandardDomain for site-wide {ref} links.

    Same pattern as ``_register_tool_labels`` but for sections with
    ``model-`` prefixed IDs.
    """
    domain = t.cast("StandardDomain", app.env.get_domain("std"))
    docname = app.env.docname
    for section in doctree.findall(nodes.section):
        if not section["ids"]:
            continue
        section_id = section["ids"][0]
        if not section_id.startswith("model-"):
            continue
        if section.children and isinstance(section[0], nodes.title):
            title_node = section[0]
            model_name = ""
            for child in title_node.children:
                if isinstance(child, nodes.literal):
                    model_name = child.astext()
                    break
            if not model_name:
                continue
            domain.anonlabels[section_id] = (docname, section_id)
            domain.labels[section_id] = (docname, section_id, model_name)


def _resolve_resource_refs(
    app: Sphinx,
    doctree: nodes.document,
    fromdocname: str,
) -> None:
    """Resolve ``{resource}`` and ``{resourceref}`` placeholders.

    ``{resource}`` renders as ``code`` + resource badge.
    ``{resourceref}`` renders as ``code`` only (no badge).
    """
    domain = t.cast("StandardDomain", app.env.get_domain("std"))
    builder = app.builder

    for node in list(doctree.findall(_resource_ref_placeholder)):
        target = node.get("reftarget", "")
        show_badge = node.get("show_badge", True)
        # Try resource-prefixed label
        label_key = f"resource-{target}"
        label_info = domain.labels.get(label_key)
        if label_info is None:
            node.replace_self(nodes.literal("", target.replace("-", "_")))
            continue

        todocname, labelid, _title = label_info
        resource_name = target.replace("-", "_")

        newnode = nodes.reference("", "", internal=True)
        try:
            newnode["refuri"] = builder.get_relative_uri(fromdocname, todocname)
            if labelid:
                newnode["refuri"] += "#" + labelid
        except Exception:
            logger.warning(
                "fastmcp_autodoc: failed to resolve URI for %s -> %s",
                fromdocname,
                todocname,
            )
            newnode["refuri"] = "#" + labelid
        newnode["classes"].append("reference")
        newnode["classes"].append("internal")

        newnode += nodes.literal("", resource_name)
        if show_badge:
            newnode += nodes.Text(" ")
            newnode += _resource_badge()

        node.replace_self(newnode)


def _resolve_model_refs(
    app: Sphinx,
    doctree: nodes.document,
    fromdocname: str,
) -> None:
    """Resolve ``{model}`` and ``{modelref}`` placeholders.

    ``{model}`` renders as ``code`` + model badge.
    ``{modelref}`` renders as ``code`` only (no badge).
    """
    domain = t.cast("StandardDomain", app.env.get_domain("std"))
    builder = app.builder

    for node in list(doctree.findall(_model_ref_placeholder)):
        target = node.get("reftarget", "")
        show_badge = node.get("show_badge", True)
        # Try model-prefixed label
        label_key = f"model-{target}"
        label_info = domain.labels.get(label_key)
        if label_info is None:
            node.replace_self(nodes.literal("", target))
            continue

        todocname, labelid, _title = label_info

        newnode = nodes.reference("", "", internal=True)
        try:
            newnode["refuri"] = builder.get_relative_uri(fromdocname, todocname)
            if labelid:
                newnode["refuri"] += "#" + labelid
        except Exception:
            logger.warning(
                "fastmcp_autodoc: failed to resolve URI for %s -> %s",
                fromdocname,
                todocname,
            )
            newnode["refuri"] = "#" + labelid
        newnode["classes"].append("reference")
        newnode["classes"].append("internal")

        newnode += nodes.literal("", target)
        if show_badge:
            newnode += nodes.Text(" ")
            newnode += _model_badge()

        node.replace_self(newnode)


def setup(app: Sphinx) -> ExtensionMetadata:
    """Register the fastmcp_autodoc extension."""
    # Nodes
    app.add_node(
        _safety_badge_node,
        html=(_visit_safety_badge_html, _depart_safety_badge_html),
    )
    app.add_node(
        _resource_badge_node,
        html=(_visit_resource_badge_html, _depart_resource_badge_html),
    )
    app.add_node(
        _model_badge_node,
        html=(_visit_model_badge_html, _depart_model_badge_html),
    )

    # Collection
    app.connect("builder-inited", _collect_all)

    # Label registration
    app.connect("doctree-read", _register_tool_labels)
    app.connect("doctree-read", _register_resource_labels)
    app.connect("doctree-read", _register_model_labels)

    # Ref resolution
    app.connect("doctree-resolved", _add_section_badges)
    app.connect("doctree-resolved", _resolve_tool_refs)
    app.connect("doctree-resolved", _resolve_resource_refs)
    app.connect("doctree-resolved", _resolve_model_refs)

    # Tool roles
    app.add_role("tool", _tool_role)
    app.add_role("toolref", _toolref_role)
    app.add_role("toolicon", _toolicon_role)
    app.add_role("tooliconl", _tooliconl_role)
    app.add_role("tooliconr", _tooliconr_role)
    app.add_role("tooliconil", _tooliconil_role)
    app.add_role("tooliconir", _tooliconir_role)
    app.add_role("badge", _badge_role)

    # Resource roles
    app.add_role("resource", _resource_role)
    app.add_role("resourceref", _resourceref_role)

    # Model roles
    app.add_role("model", _model_role)
    app.add_role("modelref", _modelref_role)

    # Tool directives
    app.add_directive("fastmcp-tool", FastMCPToolDirective)
    app.add_directive("fastmcp-tool-input", FastMCPToolInputDirective)
    app.add_directive("fastmcp-toolsummary", FastMCPToolSummaryDirective)

    # Resource directives
    app.add_directive("fastmcp-resource", FastMCPResourceDirective)
    app.add_directive("fastmcp-resourcesummary", FastMCPResourceSummaryDirective)

    # Model directives
    app.add_directive("fastmcp-model", FastMCPModelDirective)
    app.add_directive("fastmcp-model-fields", FastMCPModelFieldsDirective)
    app.add_directive("fastmcp-modelsummary", FastMCPModelSummaryDirective)

    # CSS
    app.add_css_file("css/fastmcp_autodoc.css")

    return {
        "version": "0.1.0",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
