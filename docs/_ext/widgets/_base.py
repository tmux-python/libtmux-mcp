"""Base class for widgets and the docutils node that wraps rendered output."""

from __future__ import annotations

import abc
import collections.abc
import pathlib
import typing as t

import jinja2
import markupsafe
from docutils import nodes
from sphinx.builders.html import StandaloneHTMLBuilder

if t.TYPE_CHECKING:
    from sphinx.environment import BuildEnvironment
    from sphinx.writers.html5 import HTML5Translator


class HighlightFilter(t.Protocol):
    """Callable signature for the Jinja ``highlight`` filter."""

    def __call__(self, code: str, language: str = "default") -> markupsafe.Markup: ...


class CooldownDaysSlotFilter(t.Protocol):
    """Callable signature for the Jinja ``cooldown_days_slot`` filter."""

    def __call__(self, html: object) -> markupsafe.Markup: ...


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
        jenv.filters["cooldown_days_slot"] = make_cooldown_days_slot_filter()
        template = jenv.from_string(source)
        context: dict[str, t.Any] = {
            **cls.default_options,
            **options,
            **cls.context(env),
            "widget_name": cls.name,
        }
        return template.render(**context)


def make_cooldown_days_slot_filter() -> CooldownDaysSlotFilter:
    """Return a Jinja filter that injects cooldown slot ``<span>``s.

    The Pygments highlighter escapes two days-mode sentinels emitted in
    snippet bodies (see :mod:`docs._ext.widgets.mcp_install`):

    * ``&lt;COOLDOWN_DURATION&gt;`` — used by uvx and pip days bodies.
      Swapped for a span whose default text content is ``P<N>D`` (ISO
      8601 duration). uv stores the value as
      ``ExcludeNewerValue::Relative(ExcludeNewerSpan)`` and recomputes
      ``now - N days`` on every resolver call; pip 26.1+ does the same
      at flag-parse time per invocation. The snippet stays fresh
      forever once saved to an MCP config.
    * ``&lt;COOLDOWN_DATE&gt;`` — used by pipx days bodies because
      pipx 1.8.0 bundles a pip older than 26.1 that rejects the
      duration form with ``Invalid isoformat``. Swapped for a span
      whose default text content is an absolute ISO date
      (``today - default-days``). Drifts daily but ``widget.js``
      refreshes the slot on every page load.

    Both spans live inside a Pygments string-literal span and inherit
    the parent's color. Their ``textContent`` is rewritten by
    ``widget.js`` whenever the user changes the days input. The filter
    is a no-op for outputs without either sentinel (off and bypass
    cooldown modes never emit one).
    """
    from .mcp_install import DEFAULT_COOLDOWN_DAYS, default_cooldown_date

    default_date = default_cooldown_date(DEFAULT_COOLDOWN_DAYS)
    duration_span = (
        '<span class="lm-mcp-install__cooldown-days"'
        f" data-cooldown-duration-slot>P{DEFAULT_COOLDOWN_DAYS}D</span>"
    )
    date_span = (
        '<span class="lm-mcp-install__cooldown-days"'
        f" data-cooldown-date-slot>{default_date}</span>"
    )

    def _filter(html: object) -> markupsafe.Markup:
        s = str(html)
        s = s.replace("&lt;COOLDOWN_DURATION&gt;", duration_span)
        s = s.replace("&lt;COOLDOWN_DATE&gt;", date_span)
        return markupsafe.Markup(s)

    return _filter


def make_highlight_filter(env: BuildEnvironment) -> HighlightFilter:
    r"""Return a Jinja filter that runs Sphinx's Pygments highlighter.

    Output matches ``sphinx.writers.html5.HTML5Translator.visit_literal_block``
    byte-for-byte: the inner ``highlight_block`` call already returns
    ``<div class="highlight"><pre>...</pre></div>\n``; we wrap it with the
    ``<div class="highlight-{lang} notranslate">...</div>\n`` starttag Sphinx
    produces. This means sphinx-copybutton's default selector
    (``div.highlight pre``) matches and the prompt-strip regex from gp-sphinx's
    ``DEFAULT_COPYBUTTON_PROMPT_TEXT`` works automatically.

    ``highlighter`` is declared on ``StandaloneHTMLBuilder`` and its subclasses
    (``DirectoryHTMLBuilder``, ``SingleFileHTMLBuilder``), not on the ``Builder``
    base. For non-HTML builders (``text``, ``linkcheck``, ``gettext``, ``man``,
    ...), fall back to an HTML-escaped ``<pre>`` block; it still flows through
    the ``nodes.raw("html", ...)`` output path and is harmlessly ignored by
    non-HTML writers.
    """
    builder = env.app.builder
    if isinstance(builder, StandaloneHTMLBuilder):
        highlighter = builder.highlighter

        def _highlight(code: str, language: str = "default") -> markupsafe.Markup:
            inner = highlighter.highlight_block(code, language)
            return markupsafe.Markup(
                f'<div class="highlight-{language} notranslate">{inner}</div>\n'
            )
    else:

        def _highlight(code: str, language: str = "default") -> markupsafe.Markup:
            escaped = markupsafe.escape(code)
            return markupsafe.Markup(f"<pre>{escaped}</pre>\n")

    return _highlight
