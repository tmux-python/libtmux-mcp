"""Base class for widgets and the docutils node that wraps rendered output."""

from __future__ import annotations

import abc
import collections.abc
import pathlib
import typing as t

import jinja2
import markupsafe
from docutils import nodes

if t.TYPE_CHECKING:
    from sphinx.environment import BuildEnvironment
    from sphinx.writers.html5 import HTML5Translator


class HighlightFilter(t.Protocol):
    """Callable signature for the Jinja ``highlight`` filter."""

    def __call__(self, code: str, language: str = "default") -> markupsafe.Markup: ...


class widget_container(nodes.container):  # type: ignore[misc]  # docutils nodes are untyped
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


class BaseWidget(abc.ABC):
    """Base class every concrete widget subclasses.

    Subclasses declare ``name`` plus optional ``option_spec`` / ``default_options``
    and may override ``context(env)`` to feed data into the Jinja template.
    Assets (``widget.html``, ``widget.js``, ``widget.css``) live at
    ``<srcdir>/_widgets/<name>/``; only ``widget.html`` is required.
    """

    name: t.ClassVar[str]
    option_spec: t.ClassVar[
        collections.abc.Mapping[str, collections.abc.Callable[[str], t.Any]]
    ] = {}
    default_options: t.ClassVar[collections.abc.Mapping[str, t.Any]] = {}

    @classmethod
    def assets_dir(cls, srcdir: pathlib.Path) -> pathlib.Path:
        return srcdir / "_widgets" / cls.name

    @classmethod
    def template_path(cls, srcdir: pathlib.Path) -> pathlib.Path:
        return cls.assets_dir(srcdir) / "widget.html"

    @classmethod
    def has_asset(cls, srcdir: pathlib.Path, filename: str) -> bool:
        return (cls.assets_dir(srcdir) / filename).is_file()

    @classmethod
    def context(cls, env: BuildEnvironment) -> collections.abc.Mapping[str, t.Any]:
        """Return extra Jinja context. Override in subclasses for widget data."""
        return {}

    @classmethod
    def render(
        cls,
        *,
        options: collections.abc.Mapping[str, t.Any],
        env: BuildEnvironment,
    ) -> str:
        """Render the Jinja template with merged context, return HTML."""
        template_path = cls.template_path(pathlib.Path(env.srcdir))
        source = template_path.read_text(encoding="utf-8")
        jenv = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            autoescape=jinja2.select_autoescape(["html"]),
            keep_trailing_newline=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        jenv.filters["highlight"] = make_highlight_filter(env)
        template = jenv.from_string(source)
        context: dict[str, t.Any] = {
            **cls.default_options,
            **options,
            **cls.context(env),
            "widget_name": cls.name,
        }
        return template.render(**context)


def make_highlight_filter(env: BuildEnvironment) -> HighlightFilter:
    r"""Return a Jinja filter that runs Sphinx's Pygments highlighter.

    Output matches ``sphinx.writers.html5.HTML5Translator.visit_literal_block``
    byte-for-byte (sphinx/writers/html5.py:604-630): the inner ``highlight_block``
    call already returns ``<div class="highlight"><pre>...</pre></div>\n``; we
    wrap it with the ``<div class="highlight-{lang} notranslate">...</div>\n``
    starttag Sphinx produces. This means sphinx-copybutton's default selector
    (``div.highlight pre``) matches and the prompt-strip regex from gp-sphinx's
    ``DEFAULT_COPYBUTTON_PROMPT_TEXT`` works automatically.
    """
    # ``highlighter`` is declared on StandaloneHTMLBuilder
    # (sphinx/builders/html/__init__.py:246), not on the Builder base mypy
    # sees here -- hence the type-ignore. Callers are guaranteed an HTML
    # builder by the ``builder.format == "html"`` guard in
    # ``install_widget_assets``.
    highlighter = env.app.builder.highlighter  # type: ignore[attr-defined]

    def _highlight(code: str, language: str = "default") -> markupsafe.Markup:
        inner = highlighter.highlight_block(code, language)
        return markupsafe.Markup(
            f'<div class="highlight-{language} notranslate">{inner}</div>\n'
        )

    return _highlight
