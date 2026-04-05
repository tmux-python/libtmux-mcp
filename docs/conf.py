"""Sphinx configuration for libtmux-mcp."""

from __future__ import annotations

import pathlib
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
    extra_extensions=["sphinx.ext.todo", "fastmcp_autodoc"],
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

_gp_setup = conf.pop("setup")


def setup(app: Sphinx) -> None:
    """Configure Sphinx app hooks and register project-specific JS."""
    _gp_setup(app)
    app.add_js_file("js/prompt-copy.js", loading_method="defer")


globals().update(conf)
