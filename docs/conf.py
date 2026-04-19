"""Sphinx configuration for libtmux-mcp."""

from __future__ import annotations

import pathlib
import re
import sys
import typing as t

from gp_sphinx.config import make_linkcode_resolve, merge_sphinx_config

import libtmux_mcp

if t.TYPE_CHECKING:
    from sphinx.application import Sphinx

# Get the project root dir, which is the parent dir of this
cwd = pathlib.Path(__file__).parent
project_root = cwd.parent
project_src = project_root / "src"

sys.path.insert(0, str(project_src))
sys.path.insert(0, str(cwd / "_ext"))

# package data
about: dict[str, str] = {}
with (project_src / "libtmux_mcp" / "__about__.py").open() as fp:
    exec(fp.read(), about)

conf = merge_sphinx_config(
    project=about["__title__"],
    version=about["__version__"],
    copyright=about["__copyright__"],
    source_repository=f"{about['__repository__']}/",
    docs_url=about["__url__"],
    source_branch="main",
    light_logo="img/libtmux.svg",
    dark_logo="img/libtmux.svg",
    extra_extensions=[
        "sphinx_autodoc_api_style",
        "sphinx.ext.todo",
        "sphinx_autodoc_fastmcp",
        "widgets",
    ],
    intersphinx_mapping={
        "python": ("https://docs.python.org/", None),
        "pytest": ("https://docs.pytest.org/en/stable/", None),
        "libtmux": ("https://libtmux.git-pull.com/", None),
        "pydantic": ("https://docs.pydantic.dev/latest/", None),
    },
    linkcode_resolve=make_linkcode_resolve(
        libtmux_mcp,
        about["__repository__"],
    ),
    theme_options={
        "announcement": (
            "<em>Pre-alpha.</em> APIs may change."
            " <a href='https://github.com/tmux-python/libtmux-mcp/issues'>"
            "Feedback welcome</a>."
        ),
    },
    html_favicon="_static/favicon.ico",
    html_extra_path=["manifest.json"],
    rediraffe_redirects="redirects.txt",
    copybutton_selector="div.highlight pre, div.admonition.prompt > p:last-child",
    copybutton_exclude=".linenos, .admonition-title",
)

conf["myst_enable_extensions"] = [*conf["myst_enable_extensions"], "attrs_inline"]

conf["fastmcp_tool_modules"] = [
    "libtmux_mcp.tools.server_tools",
    "libtmux_mcp.tools.session_tools",
    "libtmux_mcp.tools.window_tools",
    "libtmux_mcp.tools.pane_tools",
    "libtmux_mcp.tools.option_tools",
    "libtmux_mcp.tools.env_tools",
    "libtmux_mcp.tools.buffer_tools",
    "libtmux_mcp.tools.wait_for_tools",
    "libtmux_mcp.tools.hook_tools",
]
conf["fastmcp_area_map"] = {
    "server_tools": "sessions",
    "session_tools": "sessions",
    "window_tools": "windows",
    "pane_tools": "panes",
    "option_tools": "options",
    "env_tools": "options",
    "buffer_tools": "buffers",
    "wait_for_tools": "waits",
    "hook_tools": "hooks",
}
conf["fastmcp_server_module"] = "libtmux_mcp.server:mcp"
conf["fastmcp_model_module"] = "libtmux_mcp.models"
conf["fastmcp_model_classes"] = (
    "SessionInfo",
    "WindowInfo",
    "PaneInfo",
    "PaneContentMatch",
    "SearchPanesResult",
    "PaneSnapshot",
    "ServerInfo",
    "OptionResult",
    "OptionSetResult",
    "EnvironmentResult",
    "EnvironmentSetResult",
    "WaitForTextResult",
    "ContentChangeResult",
    "HookEntry",
    "HookListResult",
    "BufferRef",
    "BufferContent",
)
conf["fastmcp_section_badge_map"] = {
    "Inspect": "readonly",
    "Act": "mutating",
    "Destroy": "destructive",
}
conf["fastmcp_section_badge_pages"] = ("tools/index", "index")

_gp_setup = conf.pop("setup")

# Matches Pydantic-style markdown cross-refs in RST docstrings:
#   [DisplayText][qualified.Name]       →  :any:`DisplayText <qualified.Name>`
#   [`DisplayText`][qualified.Name]     →  :any:`DisplayText <qualified.Name>`
# Display text may be wrapped in backticks — strip them before forming the role.
_MD_XREF = re.compile(r"\[`?([^`\]]+)`?\]\[([a-zA-Z_][a-zA-Z0-9_.]*)\]")


def _convert_md_xrefs(
    app: Sphinx,
    what: str,
    name: str,
    obj: object,
    options: object,
    lines: list[str],
) -> None:
    """Rewrite Pydantic markdown cross-refs to RST :any: roles."""
    for i, line in enumerate(lines):
        lines[i] = _MD_XREF.sub(r":any:`\1 <\2>`", line)


def setup(app: Sphinx) -> None:
    """Configure Sphinx app hooks and register project-specific JS/CSS."""
    _gp_setup(app)
    app.connect("autodoc-process-docstring", _convert_md_xrefs)
    app.add_js_file("js/prompt-copy.js", loading_method="defer")
    app.add_css_file("css/project-admonitions.css")


globals().update(conf)
