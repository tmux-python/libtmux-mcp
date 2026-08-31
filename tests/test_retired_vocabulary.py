"""Retired toolset identifiers, configuration, and headings stay retired."""

from __future__ import annotations

import pathlib
import re

RETIRED_IDENTIFIERS = frozenset(
    {
        "ANNOTATIONS_ALLOCATE",
        "ANNOTATIONS_CHANGE",
        "ANNOTATIONS_DEFERRED_EXEC",
        "ANNOTATIONS_DELETE",
        "ANNOTATIONS_OBSERVE",
        "ANNOTATIONS_OBSERVE_CONTENT",
        "ANNOTATIONS_PANE_INPUT",
        "ANNOTATIONS_SPAWN",
        "InspectRetryMiddleware",
        "ReadonlyRetryMiddleware",
        "SafetyMiddleware",
        "TAG_DESTRUCTIVE",
        "TAG_MUTATING",
        "TAG_READONLY",
        "VALID_SAFETY_LEVELS",
        "call_destructive_tools_batch",
        "call_mutating_tools_batch",
        "call_readonly_tools_batch",
    }
)

RETIRED_CONFIG_KEYS = frozenset({"LIBTMUX_SAFETY"})

RETIRED_HEADINGS = frozenset(
    {
        "Discovery vs. mutation",
        "Safety levels",
        "Safety tiers",
    }
)

_RETIRED_TERM_PATTERN = re.compile(
    r"\b(?:safety[ -]tiers?|mutating tools?|default tiers?)\b",
    re.IGNORECASE,
)

HISTORICAL_FILES = frozenset({pathlib.Path("CHANGES"), pathlib.Path("MIGRATION")})

CONFIG_REJECTION_FILES = frozenset(
    {
        pathlib.Path("scripts/mcp_swap.py"),
        pathlib.Path("src/libtmux_mcp/server.py"),
        pathlib.Path("tests/test_mcp_swap.py"),
        pathlib.Path("tests/test_server.py"),
    }
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_IDENTIFIER_PATTERN = re.compile(
    rf"\b(?:{'|'.join(map(re.escape, sorted(RETIRED_IDENTIFIERS)))})\b"
)
_CONFIG_PATTERN = re.compile(
    rf"\b(?:{'|'.join(map(re.escape, sorted(RETIRED_CONFIG_KEYS)))})\b"
)
_HEADING_PATTERN = re.compile(
    rf"^#{{1,6}}\s+(?:{'|'.join(map(re.escape, sorted(RETIRED_HEADINGS)))})\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _tracked_sources() -> list[pathlib.Path]:
    """Return source, tests, scripts, skills, and user documentation."""
    paths: set[pathlib.Path] = set()
    for pattern in (
        ".agents/**/*.md",
        ".github/**/*.md",
        "src/**/*.py",
        "tests/**/*.py",
        "scripts/**/*.py",
        "docs/**/*.md",
        "*.md",
    ):
        paths.update(
            path
            for path in _ROOT.glob(pattern)
            if "_build" not in path.parts and path != pathlib.Path(__file__).resolve()
        )
    paths.add(_ROOT / "CHANGES")
    paths.add(_ROOT / "MIGRATION")
    paths.add(_ROOT / "pyproject.toml")
    return sorted(paths)


def test_retired_contract_names_do_not_return() -> None:
    """Removed API names stay gone without banning ordinary security prose."""
    offenders: list[str] = []

    for path in _tracked_sources():
        relative = path.relative_to(_ROOT)
        if relative in HISTORICAL_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{relative}: term {match.group(0)}"
            for match in _RETIRED_TERM_PATTERN.finditer(text)
        )
        offenders.extend(
            f"{relative}: identifier {match.group(0)}"
            for match in _IDENTIFIER_PATTERN.finditer(text)
        )
        if relative not in CONFIG_REJECTION_FILES:
            offenders.extend(
                f"{relative}: config {match.group(0)}"
                for match in _CONFIG_PATTERN.finditer(text)
            )
        if path.suffix == ".md":
            offenders.extend(
                f"{relative}: heading {match.group(0)}"
                for match in _HEADING_PATTERN.finditer(text)
            )

    assert not offenders, "Retired contract names returned:\n" + "\n".join(offenders)


def test_retired_term_pattern_uses_whole_tokens() -> None:
    """Ordinary words containing the retired stems remain legal."""
    assert _RETIRED_TERM_PATTERN.search("a safety tier")
    assert _RETIRED_TERM_PATTERN.search("a mutating tool")
    assert _RETIRED_TERM_PATTERN.search("the default tier")
    assert _RETIRED_TERM_PATTERN.search("entire mutations") is None
