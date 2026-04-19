"""Base class for widgets and the docutils node that wraps rendered output."""

from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Mapping

import jinja2
from docutils import nodes

if TYPE_CHECKING:
    from sphinx.environment import BuildEnvironment
    from sphinx.writers.html5 import HTML5Translator


class widget_container(nodes.container):
    """Wraps a widget's rendered HTML; visit/depart emit the outer div."""


def visit_widget_container(
    translator: HTML5Translator,
    node: widget_container,
) -> None:
    """Open ``<div class="lm-widget lm-widget-{name}">`` for the widget."""
    name = node["widget_name"]
    translator.body.append(
        f'<div class="lm-widget lm-widget-{name}" data-widget="{name}">'
    )


def depart_widget_container(
    translator: HTML5Translator,
    node: widget_container,
) -> None:
    """Close the widget wrapper div."""
    translator.body.append("</div>")


ASSET_FILES: tuple[str, ...] = ("widget.html", "widget.js", "widget.css")


class BaseWidget(ABC):
    """Base class every concrete widget subclasses.

    Subclasses declare ``name`` plus optional ``option_spec`` / ``default_options``
    and may override ``context(env)`` to feed data into the Jinja template.
    Assets (``widget.html``, ``widget.js``, ``widget.css``) live at
    ``<srcdir>/_widgets/<name>/``; only ``widget.html`` is required.
    """

    name: ClassVar[str]
    option_spec: ClassVar[Mapping[str, Callable[[str], Any]]] = {}
    default_options: ClassVar[Mapping[str, Any]] = {}

    @classmethod
    def assets_dir(cls, srcdir: Path) -> Path:
        return srcdir / "_widgets" / cls.name

    @classmethod
    def template_path(cls, srcdir: Path) -> Path:
        return cls.assets_dir(srcdir) / "widget.html"

    @classmethod
    def has_asset(cls, srcdir: Path, filename: str) -> bool:
        return (cls.assets_dir(srcdir) / filename).is_file()

    @classmethod
    def context(cls, env: BuildEnvironment) -> Mapping[str, Any]:
        """Return extra Jinja context. Override in subclasses for widget data."""
        return {}

    @classmethod
    def render(
        cls,
        *,
        options: Mapping[str, Any],
        env: BuildEnvironment,
    ) -> str:
        """Render the Jinja template with merged context, return HTML."""
        template_path = cls.template_path(Path(env.srcdir))
        source = template_path.read_text(encoding="utf-8")
        jenv = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            autoescape=jinja2.select_autoescape(["html"]),
            keep_trailing_newline=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template = jenv.from_string(source)
        context: dict[str, Any] = {
            **cls.default_options,
            **options,
            **cls.context(env),
            "widget_name": cls.name,
        }
        return template.render(**context)
