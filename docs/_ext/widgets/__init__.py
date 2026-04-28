"""Reusable widget framework for Sphinx docs.

Each widget is a ``BaseWidget`` subclass in a sibling module (e.g.
``mcp_install.py``) plus a ``<docs>/_widgets/<name>/widget.{html,js,css}``
asset directory. Widgets autodiscover at ``setup()`` time — adding a new one
requires no registry edits. Usage from Markdown/RST:

.. code-block:: markdown

   ```{mcp-install}
   :variant: compact
   ```
"""

from __future__ import annotations

import functools
import typing as t

from ._assets import install_widget_assets
from ._base import (
    BaseWidget,
    depart_widget_container,
    visit_widget_container,
    widget_container,
)
from ._directive import make_widget_directive
from ._discovery import discover
from ._prehydrate import inject_mcp_install_prehydrate

if t.TYPE_CHECKING:
    from sphinx.application import Sphinx

__version__ = "0.1.0"

__all__ = [
    "BaseWidget",
    "__version__",
    "setup",
    "widget_container",
]


def setup(app: Sphinx) -> dict[str, t.Any]:
    """Register every discovered widget and wire the asset pipeline."""
    widgets = discover()

    app.add_node(
        widget_container,
        html=(visit_widget_container, depart_widget_container),
    )

    for name, widget_cls in widgets.items():
        app.add_directive(name, make_widget_directive(widget_cls))

    app.connect(
        "builder-inited",
        functools.partial(install_widget_assets, widgets=widgets),
    )
    app.connect("html-page-context", inject_mcp_install_prehydrate)

    return {
        "version": __version__,
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
