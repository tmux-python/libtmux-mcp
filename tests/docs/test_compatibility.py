"""Keep the compatibility reference in step with the manifest.

The dependency table in ``docs/reference/compatibility.md`` restates the
floors that live in ``pyproject.toml``. Nothing regenerates it, so it
drifts silently every time one moves — and a reader planning an upgrade
gets a number the resolver will never agree with.

Requirements compare both normalized version ranges and direct source URLs.
A documented source pin must match the dependency the resolver installs.
"""

from __future__ import annotations

import re
import typing as t

import pytest
import tomlkit
from packaging.requirements import Requirement
from packaging.utils import NormalizedName, canonicalize_name

if t.TYPE_CHECKING:
    import pathlib

#: A Markdown link; its text is the human-facing name, which may be
#: capitalized differently from the distribution ("FastMCP").
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _documented(text: str) -> dict[NormalizedName, Requirement]:
    """Return the ``## Dependencies`` table keyed by canonical name."""
    for chunk in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        title, _, body = chunk.partition("\n")
        if title.strip() != "Dependencies":
            continue
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in body.splitlines()
            if line.strip().startswith("|")
        ]
        requirements = {}
        for name, version, *_ in rows[2:]:
            match = _LINK.search(name)
            requirement = Requirement((match.group(1) if match else name) + version)
            requirements[canonicalize_name(requirement.name)] = requirement
        return requirements
    pytest.fail("compatibility.md has no '## Dependencies' section")


def _declared(repo_root: pathlib.Path) -> dict[NormalizedName, Requirement]:
    """Return ``[project].dependencies`` keyed by canonical name."""
    manifest = tomlkit.loads(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    ).unwrap()
    reqs = [Requirement(spec) for spec in manifest["project"]["dependencies"]]
    return {canonicalize_name(r.name): r for r in reqs}


def test_documented_dependency_ranges_match_the_manifest(
    docs_dir: pathlib.Path,
    repo_root: pathlib.Path,
) -> None:
    """Document every dependency with its enforced range or source URL."""
    text = (docs_dir / "reference" / "compatibility.md").read_text(encoding="utf-8")

    assert _documented(text) == _declared(repo_root)
