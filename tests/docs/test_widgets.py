"""Tests for the ``docs/_ext/widgets`` framework and ``MCPInstallWidget``."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from widgets import BaseWidget
from widgets._discovery import discover
from widgets.mcp_install import (
    CLIENTS,
    METHODS,
    MCPInstallWidget,
    _body_for,
    build_panels,
)

if TYPE_CHECKING:
    from sphinx.testing.fixtures import SharedResult  # noqa: F401


# ---------- unit: data layer ----------------------------------------------


def test_discover_finds_mcp_install() -> None:
    registry = discover()
    assert "mcp-install" in registry
    assert registry["mcp-install"] is MCPInstallWidget
    assert issubclass(registry["mcp-install"], BaseWidget)


def test_build_panels_yields_cross_product() -> None:
    panels = build_panels()
    assert len(panels) == len(CLIENTS) * len(METHODS)
    assert panels[0].is_default is True
    assert sum(1 for p in panels if p.is_default) == 1


def test_build_panels_first_cell_is_claude_code_uvx() -> None:
    panels = build_panels()
    assert panels[0].client.id == "claude-code"
    assert panels[0].method.id == "uvx"
    assert panels[0].language == "shell"


def test_body_for_cli_client_returns_shell_command() -> None:
    body = _body_for(CLIENTS[0], METHODS[0])  # claude-code × uvx
    assert body == "claude mcp add libtmux -- uvx libtmux-mcp"


def test_body_for_json_client_returns_config_snippet() -> None:
    claude_desktop = CLIENTS[1]
    body = _body_for(claude_desktop, METHODS[0])  # claude-desktop × uvx
    assert '"command": "uvx"' in body
    assert '"libtmux-mcp"' in body


def test_body_for_unknown_kind_raises() -> None:
    from widgets.mcp_install import Client

    fake = Client(id="x", label="X", kind="bogus", config_file="")
    with pytest.raises(ValueError, match="unknown client kind"):
        _body_for(fake, METHODS[0])


# ---------- integration: Sphinx build -------------------------------------


def _build(make_app, srcdir: Path, warning_stream: io.StringIO | None = None):
    """Helper — calls ``make_app`` with warning capture."""
    kwargs = {"srcdir": srcdir, "freshenv": True}
    if warning_stream is not None:
        kwargs["warning"] = warning_stream
    app = make_app("dirhtml", **kwargs)
    app.build()
    return app


def test_widget_renders_in_built_html(make_app, real_widget_srcdir: Path) -> None:
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    html = (Path(app.outdir) / "index.html").read_text(encoding="utf-8")
    assert 'class="lm-widget lm-widget-mcp-install"' in html
    assert 'class="lm-mcp-install lm-mcp-install--full"' in html
    assert 'data-tab-value="claude-code"' in html
    assert 'data-tab-value="uvx"' in html


def test_variant_compact_applied(make_app, real_widget_srcdir: Path) -> None:
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n:variant: compact\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    html = (Path(app.outdir) / "index.html").read_text(encoding="utf-8")
    assert "lm-mcp-install--compact" in html
    # Compact hides the config-file row.
    assert "lm-mcp-install__config-file" not in html


def test_invalid_variant_raises_warning(
    make_app,
    real_widget_srcdir: Path,
) -> None:
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n:variant: bogus\n```\n",
        encoding="utf-8",
    )
    warnings = io.StringIO()
    _build(make_app, real_widget_srcdir, warning_stream=warnings)
    assert "bogus" in warnings.getvalue().lower() or "invalid" in warnings.getvalue().lower()


def test_assets_copied_and_linked(make_app, real_widget_srcdir: Path) -> None:
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    outdir = Path(app.outdir)
    assert (outdir / "_static" / "widgets" / "mcp-install" / "widget.css").is_file()
    assert (outdir / "_static" / "widgets" / "mcp-install" / "widget.js").is_file()
    html = (outdir / "index.html").read_text(encoding="utf-8")
    assert "_static/widgets/mcp-install/widget.css" in html
    assert "_static/widgets/mcp-install/widget.js" in html


def test_missing_template_errors_cleanly(
    make_app,
    real_widget_srcdir: Path,
) -> None:
    # Remove the template to simulate a broken widget.
    (real_widget_srcdir / "_widgets" / "mcp-install" / "widget.html").unlink()
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    warnings = io.StringIO()
    _build(make_app, real_widget_srcdir, warning_stream=warnings)
    message = warnings.getvalue()
    assert "mcp-install" in message
    assert "template not found" in message or "widget.html" in message


def test_widget_dependency_noted(
    make_app,
    real_widget_srcdir: Path,
) -> None:
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    deps = app.env.dependencies.get("index", set())
    # Normalise path separators; env stores paths relative to srcdir.
    dep_strs = {str(d) for d in deps}
    assert any("mcp-install" in d and "widget.html" in d for d in dep_strs)
