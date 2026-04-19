"""Factory that manufactures a Sphinx Directive class for a given widget."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from docutils import nodes
from sphinx.util.docutils import SphinxDirective

from ._base import ASSET_FILES, BaseWidget, widget_container


def make_widget_directive(widget_cls: type[BaseWidget]) -> type[SphinxDirective]:
    """Create a ``SphinxDirective`` subclass bound to ``widget_cls``.

    Each widget gets its own Directive subclass (not a single dispatcher) because
    docutils parses ``:option:`` lines against ``option_spec`` *before* calling
    ``run()`` -- so the spec must be static per directive name.
    """

    class _WidgetDirective(SphinxDirective):
        has_content = False
        required_arguments = 0
        optional_arguments = 0
        final_argument_whitespace = False
        # Copy the widget's option_spec so per-directive mutations don't leak.
        option_spec: ClassVar[dict[str, Any]] = dict(widget_cls.option_spec)

        def run(self) -> list[nodes.Node]:
            """Render the widget and return a single ``widget_container`` node."""
            merged: dict[str, Any] = {
                **widget_cls.default_options,
                **self.options,
            }
            self._note_asset_dependencies()
            html = self._render(merged)
            container = widget_container(widget_name=widget_cls.name)
            container += nodes.raw("", html, format="html")
            self.set_source_info(container)
            return [container]

        def _render(self, options: dict[str, Any]) -> str:
            try:
                return widget_cls.render(options=options, env=self.env)
            except FileNotFoundError as exc:
                msg = (
                    f"widget {widget_cls.name!r}: template not found -- "
                    f"expected {exc.filename}"
                )
                raise self.severe(msg) from exc
            except Exception as exc:  # Jinja UndefinedError, etc.
                msg = f"widget {widget_cls.name!r} render failed: {exc}"
                raise self.error(msg) from exc

        def _note_asset_dependencies(self) -> None:
            assets_dir = widget_cls.assets_dir(Path(self.env.srcdir))
            for filename in ASSET_FILES:
                path = assets_dir / filename
                if path.is_file():
                    self.env.note_dependency(str(path))

    _WidgetDirective.__name__ = f"{widget_cls.__name__}Directive"
    _WidgetDirective.__qualname__ = _WidgetDirective.__name__
    return _WidgetDirective
