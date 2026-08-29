"""The tier vocabulary must not come back.

`readonly` / `mutating` / `destructive` described an ordered ladder this
server does not have. Tools are grouped into unordered toolsets instead.
The words are easy to reintroduce by habit, so a gate holds the line.
"""

from __future__ import annotations

import pathlib
import re

import pytest

#: Words that named the retired tiers. Matched whole and case-insensitively.
RETIRED = ("readonly", "mutating", "destructive", "safety tier", "safety level")

#: MCP defines these fields and their meanings; they are the protocol's
#: vocabulary, not ours, and they stay.
PROTOCOL_NAMES = (
    "readOnlyHint",
    "destructiveHint",
    "read_only_hint",
    "destructive_hint",
)

#: Files allowed to name what they replace.
EXEMPT = (
    "CHANGES",
    "MIGRATION.md",
    "tests/test_retired_vocabulary.py",
    # The startup error has to say the variable it is refusing, and the
    # test that proves it fires has to set it.
    "src/libtmux_mcp/server.py",
    "tests/test_server.py",
    # A redirect must name the old path to serve it.
    "docs/redirects.txt",
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PATTERN = re.compile("|".join(re.escape(word) for word in RETIRED), re.IGNORECASE)


def _tracked_sources() -> list[pathlib.Path]:
    """Return the files this gate covers."""
    paths: list[pathlib.Path] = []
    for pattern in ("src/**/*.py", "tests/**/*.py", "docs/**/*.md", "*.md"):
        paths.extend(
            path
            for path in _ROOT.glob(pattern)
            if "_build" not in path.parts and str(path.relative_to(_ROOT)) not in EXEMPT
        )
    return paths


@pytest.mark.parametrize("path", _tracked_sources(), ids=lambda p: str(p.name))
def test_no_file_reintroduces_the_tier_vocabulary(path: pathlib.Path) -> None:
    """No source or page names a tier that no longer exists."""
    text = path.read_text(encoding="utf-8")
    for name in PROTOCOL_NAMES:
        text = text.replace(name, "")

    offenders = sorted({match.group(0).lower() for match in _PATTERN.finditer(text)})

    assert not offenders, (
        f"{path.relative_to(_ROOT)} names the retired tiers {offenders}. "
        f"Tools belong to unordered toolsets: inspect, manage, execute, "
        f"teardown. If this file must name what it replaced, add it to "
        f"EXEMPT with a reason."
    )
