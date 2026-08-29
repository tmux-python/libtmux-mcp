"""Tests for caller-supplied regex screening."""

from __future__ import annotations

import re

import pytest

from libtmux_mcp._errors import ExpectedToolError
from libtmux_mcp._patterns import compile_pattern

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
    # A body that can match nothing, owed many iterations. The MINIMUM
    # decides it: the unbounded forms below are fine and stay allowed.
    r"(a?){20}b",
    r"(a?){20,}b",
    r"(a|){20}b",
    r"(a??){20}b",
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
    r"(?<=ERROR: )\w+",
    # Large repeats whose body is FIXED width: exactly one way to split,
    # so nothing backtracks.
    r"(\d{2}){20}",
    r"\s{0,20}X",
    r"(?:ab){50}$",
]


#: Refused although CPython happens to finish them quickly. The screen
#: models the PATTERN, not the engine's empty-match loop break, so a
#: variable-width body under a large repeat goes whether or not this
#: version prunes it. Costing a caller ``(a?)*`` -- which is ``a*``
#: written the long way -- is what buys refusing ``(a{0,3})*``, which
#: does not finish.
CONSERVATIVELY_REFUSED = [
    r"(a?)*b",
    r"(a?)+b",
    r"(a?){1,20}b",
    r"(ab?){20}c",
]


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_compile_pattern_refuses_uninterruptible_patterns(pattern: str) -> None:
    """An ambiguous repeat is refused before it can run."""
    with pytest.raises(ExpectedToolError, match="exponential time"):
        compile_pattern(pattern, regex=True, flags=0, label="test")


@pytest.mark.parametrize("pattern", CONSERVATIVELY_REFUSED)
def test_compile_pattern_refuses_a_variable_width_body(pattern: str) -> None:
    """Width, not the engine's pruning, decides. See the list's comment."""
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
