"""Tests keeping the compatibility reference in step with the manifest.

The dependency table in ``docs/reference/compatibility.md`` restates
floors that live in ``pyproject.toml``. Nothing regenerates it, so it
drifts silently every time a floor moves — a reader planning an upgrade
gets a number the resolver will never agree with.

Specifiers are compared as :class:`~packaging.specifiers.SpecifierSet`
values rather than as strings, so the doc stays free to write
``>= 0.62.0, < 1.0`` where the manifest writes ``>=0.62.0,<1.0``.
"""

from __future__ import annotations

import re
import typing as t

import pytest
import tomlkit
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import NormalizedName, canonicalize_name

if t.TYPE_CHECKING:
    import pathlib

#: Heading of the table that restates ``[project].dependencies``.
_DEPENDENCIES_HEADING = "Dependencies"

#: A Markdown link; its text is the human-facing package name, which may
#: be capitalized differently from the distribution ("FastMCP").
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _section_body(text: str, heading: str) -> str:
    """Return the body of a ``##`` section, up to the next one."""
    for chunk in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = chunk.partition("\n")
        if title.strip() == heading:
            return body
    pytest.fail(f"compatibility.md has no '## {heading}' section")


def _table_rows(body: str) -> list[list[str]]:
    """Return the data rows of the first Markdown table in ``body``."""
    rows = [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in body.splitlines()
        if line.strip().startswith("|")
    ]
    # Drop the header and its `|---|---|` separator.
    return [row for row in rows[2:] if row]


def _documented_floors(text: str) -> dict[NormalizedName, SpecifierSet]:
    """Return the dependency table keyed by canonical package name."""
    body = _section_body(text, _DEPENDENCIES_HEADING)
    documented = {}
    for name_cell, version_cell, *_ in _table_rows(body):
        link = _LINK.search(name_cell)
        name = link.group(1) if link else name_cell
        documented[canonicalize_name(name)] = SpecifierSet(version_cell)
    return documented


def _declared_floors(repo_root: pathlib.Path) -> dict[NormalizedName, SpecifierSet]:
    """Return ``[project].dependencies`` keyed by canonical package name."""
    manifest = tomlkit.loads(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    ).unwrap()
    requirements = [Requirement(spec) for spec in manifest["project"]["dependencies"]]
    return {canonicalize_name(req.name): req.specifier for req in requirements}


def test_compatibility_documents_every_runtime_dependency(
    docs_dir: pathlib.Path,
    repo_root: pathlib.Path,
) -> None:
    """The dependency table lists exactly what the project requires."""
    text = (docs_dir / "reference" / "compatibility.md").read_text(encoding="utf-8")

    assert set(_documented_floors(text)) == set(_declared_floors(repo_root))


def test_compatibility_dependency_floors_match_the_manifest(
    docs_dir: pathlib.Path,
    repo_root: pathlib.Path,
) -> None:
    """Each documented version range is the range the resolver enforces."""
    text = (docs_dir / "reference" / "compatibility.md").read_text(encoding="utf-8")
    documented = _documented_floors(text)

    assert documented == _declared_floors(repo_root)
