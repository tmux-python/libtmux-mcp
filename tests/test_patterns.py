"""Tests for caller-supplied regex screening."""

from __future__ import annotations

import re

import pytest

from libtmux_mcp._patterns import compile_pattern
from libtmux_mcp._utils import ExpectedToolError

#: Patterns that backtrack exponentially on a non-matching line.
#: ``(a+)+$`` against one 121-character line does not finish in three
#: minutes, and ``re`` cannot be interrupted once it starts.
CATASTROPHIC = [
    r"(a+)+$",
    r"(a|a)+$",
    r"(a*)*X$",
    r"^(a+)+#$",
    r"(a|ab)+",
    r"(\d+\.)+$",
    r"(x*)+y",
    r"((a)*)*b",
    r"(a{1,50}){1,50}$",
    r"(?=(a+)+$)b",
    r"(?!(a+)+$)b",
    r"(x)(?(1)(a+)+|b)$",
]

#: Ordinary patterns an agent would actually write. A screen that
#: refuses these is worse than the hazard it prevents.
ORDINARY = [
    r"ERROR",
    r"\d+",
    r"foo.*bar",
    r"^\s*at ",
    r"(?:ab)+$",
    r"(cat|dog)+",
    r"(\d{2}){3}",
    r"^\[(INFO|WARN|ERROR)\]",
    r"[a-z]+@[a-z]+\.com",
    r"Traceback \(most recent call last\)",
    r"a+b+c+$",
    r"https?://\S+",
    r"(foo|bar|baz)$",
    r"\bTODO\b.*",
    r"^#{1,6} ",
    r"(?=.*ERROR)^\[",
    r"(?!DEBUG)\w+$",
    r"(x)(?(1)yes|no)",
    r"(?<=ERROR: )\\w+",
]


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_compile_pattern_refuses_uninterruptible_patterns(pattern: str) -> None:
    """An ambiguous repeat is refused before it can run."""
    with pytest.raises(ExpectedToolError, match="exponential time"):
        compile_pattern(pattern, regex=True, flags=0, label="test")


@pytest.mark.parametrize("pattern", ORDINARY)
def test_compile_pattern_accepts_ordinary_patterns(pattern: str) -> None:
    """The screen must not cost callers patterns they legitimately need."""
    assert compile_pattern(pattern, regex=True, flags=0, label="test") is not None


def test_compile_pattern_never_screens_a_literal() -> None:
    """``regex=False`` is escaped, so it has no quantifiers to nest."""
    compiled = compile_pattern("(a+)+$", regex=False, flags=0, label="test")
    assert compiled.search("(a+)+$") is not None


def test_compile_pattern_still_reports_invalid_syntax() -> None:
    """Screening must not swallow the error tmux callers already relied on."""
    with pytest.raises(ExpectedToolError, match="Invalid regex pattern"):
        compile_pattern("(unclosed", regex=True, flags=0, label="test")


def test_compile_pattern_honours_flags() -> None:
    """Flags reach the compiled pattern rather than being dropped."""
    compiled = compile_pattern("abc", regex=True, flags=re.IGNORECASE, label="test")
    assert compiled.search("ABC") is not None
