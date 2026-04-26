"""Tests for the ``docs/_ext/widgets`` framework and ``MCPInstallWidget``."""

from __future__ import annotations

import io
import pathlib
import re
import textwrap
import typing as t

import pytest

from docs._ext.widgets import BaseWidget
from docs._ext.widgets._base import make_highlight_filter
from docs._ext.widgets._discovery import discover
from docs._ext.widgets.mcp_install import (
    CLIENTS,
    METHODS,
    MCPInstallWidget,
    _body_for,
    build_panels,
)

if t.TYPE_CHECKING:
    from sphinx.testing.util import SphinxTestApp

    from ._snapshots import _HTMLFragmentSnapshot

    MakeApp = t.Callable[..., SphinxTestApp]
    SnapshotHTMLFragment = _HTMLFragmentSnapshot


# ---------- unit: data layer ----------------------------------------------


def test_discover_finds_mcp_install() -> None:
    """``discover()`` returns the real ``MCPInstallWidget`` under its directive name."""
    registry = discover()
    assert "mcp-install" in registry
    assert registry["mcp-install"] is MCPInstallWidget
    assert issubclass(registry["mcp-install"], BaseWidget)


def test_build_panels_yields_cross_product() -> None:
    """One panel per (client, method) pair; exactly one flagged as default."""
    panels = build_panels()
    assert len(panels) == len(CLIENTS) * len(METHODS)
    assert panels[0].is_default is True
    assert sum(1 for p in panels if p.is_default) == 1


def test_build_panels_first_cell_is_claude_code_uvx() -> None:
    """First panel is Claude Code + uvx in ``console`` with a ``$ `` prompt body."""
    panels = build_panels()
    assert panels[0].client.id == "claude-code"
    assert panels[0].method.id == "uvx"
    assert panels[0].language == "console"
    assert panels[0].body.startswith("$ ")


def test_body_for_cli_client_returns_shell_command() -> None:
    """CLI clients get the literal ``<tool> mcp add ...`` shell command."""
    body = _body_for(CLIENTS[0], METHODS[0])  # claude-code + uvx
    assert body == "claude mcp add libtmux -- uvx libtmux-mcp"


def test_body_for_json_client_returns_config_snippet() -> None:
    """JSON clients get the ``mcpServers`` config snippet for the chosen method."""
    claude_desktop = CLIENTS[1]
    body = _body_for(claude_desktop, METHODS[0])  # claude-desktop + uvx
    assert '"command": "uvx"' in body
    assert '"libtmux-mcp"' in body


def test_body_for_unknown_kind_raises() -> None:
    """An unrecognised ``client.kind`` surfaces as a ``ValueError``."""
    from docs._ext.widgets.mcp_install import Client

    fake = Client(id="x", label="X", kind="bogus", config_file="")
    with pytest.raises(ValueError, match="unknown client kind"):
        _body_for(fake, METHODS[0])


# ---------- integration: Sphinx build -------------------------------------


def _build(
    make_app: MakeApp,
    srcdir: pathlib.Path,
    warning_stream: io.StringIO | None = None,
) -> SphinxTestApp:
    """Build a ``dirhtml`` project from ``srcdir`` with optional warning capture."""
    kwargs: dict[str, t.Any] = {"srcdir": srcdir, "freshenv": True}
    if warning_stream is not None:
        kwargs["warning"] = warning_stream
    app = make_app("dirhtml", **kwargs)
    app.build()
    return app


def test_widget_renders_in_built_html(
    make_app: MakeApp,
    real_widget_srcdir: pathlib.Path,
) -> None:
    """Directive renders a ``widget_container`` with client and method tabs."""
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    html = (pathlib.Path(app.outdir) / "index.html").read_text(encoding="utf-8")
    assert 'class="lm-widget lm-widget-mcp-install"' in html
    assert 'class="lm-mcp-install lm-mcp-install--full"' in html
    assert 'data-tab-value="claude-code"' in html
    assert 'data-tab-value="uvx"' in html


def test_variant_compact_applied(
    make_app: MakeApp,
    real_widget_srcdir: pathlib.Path,
) -> None:
    """``:variant: compact`` emits the modifier class and hides config-file rows."""
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n:variant: compact\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    html = (pathlib.Path(app.outdir) / "index.html").read_text(encoding="utf-8")
    assert "lm-mcp-install--compact" in html
    # Compact hides the config-file row.
    assert "lm-mcp-install__config-file" not in html


def test_invalid_variant_raises_warning(
    make_app: MakeApp,
    real_widget_srcdir: pathlib.Path,
) -> None:
    """An unknown ``:variant:`` value triggers a Sphinx warning at build time."""
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n:variant: bogus\n```\n",
        encoding="utf-8",
    )
    warnings = io.StringIO()
    _build(make_app, real_widget_srcdir, warning_stream=warnings)
    assert (
        "bogus" in warnings.getvalue().lower()
        or "invalid" in warnings.getvalue().lower()
    )


def test_assets_copied_and_linked(
    make_app: MakeApp,
    real_widget_srcdir: pathlib.Path,
) -> None:
    """``widget.{css,js}`` land in ``_static/widgets/<name>/`` and are linked."""
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    outdir = pathlib.Path(app.outdir)
    assert (outdir / "_static" / "widgets" / "mcp-install" / "widget.css").is_file()
    assert (outdir / "_static" / "widgets" / "mcp-install" / "widget.js").is_file()
    html = (outdir / "index.html").read_text(encoding="utf-8")
    assert "_static/widgets/mcp-install/widget.css" in html
    assert "_static/widgets/mcp-install/widget.js" in html


def test_missing_template_errors_cleanly(
    make_app: MakeApp,
    real_widget_srcdir: pathlib.Path,
) -> None:
    """Missing ``widget.html`` surfaces a clean directive-level error."""
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
    make_app: MakeApp,
    real_widget_srcdir: pathlib.Path,
) -> None:
    """``env.note_dependency`` records ``widget.html`` so edits trigger a rebuild."""
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    app = _build(make_app, real_widget_srcdir)
    deps = app.env.dependencies.get("index", set())
    # ``env.note_dependency`` stores whatever string we pass in;
    # ``_directive.py:_note_asset_dependencies`` passes absolute paths.
    dep_strs = {str(d) for d in deps}
    assert any("mcp-install" in d and "widget.html" in d for d in dep_strs)


def test_widget_renders_with_text_builder(
    make_app: MakeApp,
    real_widget_srcdir: pathlib.Path,
) -> None:
    """``{mcp-install}`` must not crash under non-HTML builders (text)."""
    (real_widget_srcdir / "index.md").write_text(
        "# Home\n\n```{mcp-install}\n```\n",
        encoding="utf-8",
    )
    app = make_app("text", srcdir=real_widget_srcdir, freshenv=True)
    app.build()  # would AttributeError before the highlight-filter isinstance fix
    assert app.statuscode == 0
    assert (pathlib.Path(app.outdir) / "index.txt").is_file()


# ---------- parity: highlight filter vs. Sphinx native literal_block ------


class HighlightCase(t.NamedTuple):
    """One (code, language) pair for the highlight-parity suite."""

    test_id: str
    code: str
    language: str


HIGHLIGHT_CASES: list[HighlightCase] = [
    HighlightCase(
        test_id="console-claude-code-uvx",
        code="$ claude mcp add libtmux -- uvx libtmux-mcp",
        language="console",
    ),
    HighlightCase(
        test_id="console-pip-prereq",
        code="$ pip install --user --upgrade libtmux libtmux-mcp",
        language="console",
    ),
    HighlightCase(
        test_id="json-mcp-config-uvx",
        code=textwrap.dedent(
            """\
            {
                "mcpServers": {
                    "libtmux": {
                        "command": "uvx",
                        "args": ["libtmux-mcp"]
                    }
                }
            }"""
        ),
        language="json",
    ),
]


@pytest.fixture
def highlight_app(make_app: MakeApp, tmp_path: pathlib.Path) -> SphinxTestApp:
    """Minimal Sphinx app with the Pygments highlighter initialised.

    ``highlighter`` is ready after ``builder.init()`` (runs during
    ``make_app()``); no ``app.build()`` needed until the native-parity test
    exercises the end-to-end ``.. code-block::`` path.
    """
    srcdir = tmp_path / "native-src"
    srcdir.mkdir()
    (srcdir / "conf.py").write_text(
        'master_doc = "index"\nextensions = []\n',
        encoding="utf-8",
    )
    (srcdir / "index.rst").write_text("Test\n====\n", encoding="utf-8")
    return make_app("html", srcdir=srcdir, freshenv=True)


def _sphinx_native_html(
    app: SphinxTestApp,
    code: str,
    language: str,
) -> str:
    """Build a real Sphinx ``code-block`` directive and extract its HTML block."""
    srcdir = pathlib.Path(app.srcdir)
    rst = (
        "Native\n======\n\n"
        f".. code-block:: {language}\n\n" + textwrap.indent(code, "   ") + "\n"
    )
    (srcdir / "native.rst").write_text(rst, encoding="utf-8")
    app.build()
    html = (pathlib.Path(app.outdir) / "native.html").read_text(encoding="utf-8")
    # Match Sphinx's highlight wrapper produced by
    # ``HTML5Translator.visit_literal_block``; the two trailing ``</div>\n``
    # (inner Pygments close + outer Sphinx close) anchor the end of the block.
    pattern = re.compile(
        rf'<div class="highlight-{re.escape(language)} notranslate">'
        r".*?</div>\n</div>\n",
        re.DOTALL,
    )
    match = pattern.search(html)
    if match is None:
        msg = "native highlight block not in rendered HTML"
        raise AssertionError(msg)
    return match.group(0)


@pytest.mark.parametrize(
    list(HighlightCase._fields),
    HIGHLIGHT_CASES,
    ids=[c.test_id for c in HIGHLIGHT_CASES],
)
def test_highlight_filter_matches_sphinx_native(
    test_id: str,
    code: str,
    language: str,
    highlight_app: SphinxTestApp,
    snapshot_html_fragment: SnapshotHTMLFragment,
) -> None:
    """Widget filter output is byte-identical to Sphinx's native ``literal_block``."""
    widget_html = str(make_highlight_filter(highlight_app.env)(code, language))
    native_html = _sphinx_native_html(highlight_app, code, language)

    assert widget_html == native_html, "widget filter diverged from Sphinx native path"
    snapshot_html_fragment(widget_html, name=f"highlight_{test_id}")


@pytest.mark.parametrize(
    list(HighlightCase._fields),
    HIGHLIGHT_CASES,
    ids=[c.test_id for c in HIGHLIGHT_CASES],
)
def test_highlight_filter_emits_copybutton_compatible_wrapper(
    test_id: str,
    code: str,
    language: str,
    highlight_app: SphinxTestApp,
) -> None:
    """Output matches sphinx-copybutton's default selector (``div.highlight pre``)."""
    html = str(make_highlight_filter(highlight_app.env)(code, language))
    assert html.startswith(f'<div class="highlight-{language} notranslate">')
    assert '<div class="highlight">' in html
    assert "<pre>" in html
    assert html.endswith("</div>\n")


def test_highlight_filter_marks_console_prompt_as_gp(
    highlight_app: SphinxTestApp,
) -> None:
    """Shell-session lexer must tag ``$ `` as ``Generic.Prompt`` (``class="gp"``)."""
    html = str(make_highlight_filter(highlight_app.env)("$ echo hi", "console"))
    assert '<span class="gp">$ </span>' in html
