"""pytest config for widget tests: wire Sphinx's test fixtures + path.

``sphinx.testing.fixtures`` provides ``make_app``, ``app``, etc. that build a
throw-away Sphinx project in a tmp dir. We also add ``docs/_ext`` to
``sys.path`` so tests can import the ``widgets`` extension the same way
``conf.py`` does in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOCS_DIR = _REPO_ROOT / "docs"
_EXT_DIR = _DOCS_DIR / "_ext"
if str(_EXT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXT_DIR))


@pytest.fixture
def docs_dir() -> Path:
    """Absolute path to ``<repo>/docs``."""
    return _DOCS_DIR


@pytest.fixture
def real_widget_srcdir(tmp_path: Path, docs_dir: Path) -> Path:
    """Minimal Sphinx srcdir pre-populated with the real ``mcp-install`` widget."""
    srcdir = tmp_path / "src"
    srcdir.mkdir()

    # Copy the real widget assets so the directive can find them.
    widgets_src = docs_dir / "_widgets" / "mcp-install"
    widgets_dst = srcdir / "_widgets" / "mcp-install"
    widgets_dst.mkdir(parents=True)
    for asset in ("widget.html", "widget.js", "widget.css"):
        (widgets_dst / asset).write_bytes((widgets_src / asset).read_bytes())

    (srcdir / "conf.py").write_text(
        f"""
import sys
sys.path.insert(0, {str(_EXT_DIR)!r})
extensions = ["myst_parser", "widgets"]
exclude_patterns = ["_build"]
master_doc = "index"
source_suffix = {{".md": "markdown"}}
""",
        encoding="utf-8",
    )
    return srcdir
