"""Copy widget assets into ``_static/widgets/<name>/`` and register them."""

from __future__ import annotations

import pathlib
import typing as t

from sphinx.util import logging
from sphinx.util.fileutil import copy_asset_file

from ._base import BaseWidget

if t.TYPE_CHECKING:
    from sphinx.application import Sphinx

logger = logging.getLogger(__name__)

STATIC_SUBDIR = "widgets"


def install_widget_assets(
    app: Sphinx,
    widgets: dict[str, type[BaseWidget]],
) -> None:
    """Copy each widget's ``widget.{css,js}`` into ``_static/widgets/<name>/``.

    Assets are then registered via ``app.add_css_file`` / ``app.add_js_file`` so
    every page includes them (same pattern as ``sphinx-copybutton``). This is
    intentionally simpler than per-page inclusion — the files are small and the
    docs are not bandwidth-constrained.
    """
    if app.builder.format != "html":
        return

    srcdir = pathlib.Path(app.srcdir)
    outdir_static = pathlib.Path(app.outdir) / "_static" / STATIC_SUBDIR

    for name, widget_cls in widgets.items():
        asset_dir = widget_cls.assets_dir(srcdir)
        dest = outdir_static / name

        for filename, register in (
            ("widget.css", app.add_css_file),
            ("widget.js", app.add_js_file),
        ):
            source = asset_dir / filename
            if not source.is_file():
                continue
            dest.mkdir(parents=True, exist_ok=True)
            copy_asset_file(str(source), str(dest))
            register(f"{STATIC_SUBDIR}/{name}/{filename}")
