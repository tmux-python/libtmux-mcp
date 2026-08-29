"""Caller-supplied regex compilation with a backtracking bound.

``search_panes`` and ``wait_for_text`` both take a regex from the
caller and run it over pane content. Python's ``re`` backtracks, has no
step limit, and cannot be interrupted from another thread -- so a
pattern like ``(a+)+$`` against one 121-character line does not finish
in three minutes, and nothing downstream can stop it.

That breaks two different promises. ``search_panes`` is readonly-tier
and sixteen concurrent calls exhaust the worker pool, after which every
tool is unresponsive. ``wait_for_text`` documents a ``timeout``, checks
it between poll iterations, and a match that never returns never
reaches the check -- so the caller who defends themselves with a small
timeout is exactly the one it does not protect.

The bound has to be the pattern, because neither a deadline nor a
worker cap can reclaim a thread stuck inside ``re``. Ambiguous repeats
are refused before they run.
"""

from __future__ import annotations

import re
import typing as t

from libtmux_mcp._utils import ExpectedToolError

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

try:  # Python 3.11+
    import re._constants as _re_constants  # type: ignore[import-not-found]
    import re._parser as _re_parser  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - Python 3.10
    import sre_constants as _re_constants
    import sre_parse as _re_parser

_MAXREPEAT = _re_constants.MAXREPEAT
_REPEAT_OPS = frozenset(
    getattr(_re_constants, name)
    for name in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
    if hasattr(_re_constants, name)
)
_BRANCH = _re_constants.BRANCH
_SUBPATTERN = _re_constants.SUBPATTERN
_ATOMIC_GROUP = getattr(_re_constants, "ATOMIC_GROUP", None)
_LITERAL = _re_constants.LITERAL
_NOT_LITERAL = _re_constants.NOT_LITERAL
_IN = _re_constants.IN
_ANY = _re_constants.ANY

#: A repeat this large is treated as unbounded for nesting purposes.
#: ``(\d{2}){3}`` is six characters of work; ``(a{1,50}){1,50}`` is a
#: bomb with a number in front of it.
_LARGE_REPEAT = 20


def _repeat_is_large(minimum: int, maximum: int) -> bool:
    """Return True when a repeat can iterate enough times to matter."""
    return maximum is _MAXREPEAT or maximum >= _LARGE_REPEAT or maximum > minimum > 0


def _contains_large_repeat(node: Iterable[t.Any]) -> bool:
    """Return True when any repeat inside *node* can iterate freely."""
    for op, av in node:
        if op in _REPEAT_OPS:
            minimum, maximum, _ = av
            if _repeat_is_large(minimum, maximum):
                return True
            if _contains_large_repeat(av[2]):
                return True
        elif op is _SUBPATTERN:
            if _contains_large_repeat(av[3]):
                return True
        elif op is _BRANCH:
            if any(_contains_large_repeat(branch) for branch in av[1]):
                return True
        elif op is _ATOMIC_GROUP and _contains_large_repeat(av):
            return True
    return False


def _first_characters(branch: Iterable[t.Any]) -> set[t.Any] | None:
    """Characters a branch can start with, or ``None`` if unbounded.

    ``None`` also covers an EMPTY branch, which is the worst case: a
    repeat whose body matches nothing at all is ambiguous at every
    position.
    """
    for op, av in branch:
        if op is _LITERAL:
            return {av}
        if op is _IN or op is _NOT_LITERAL or op is _ANY:
            return None
        if op is _SUBPATTERN:
            return _first_characters(av[3])
        if op is _BRANCH:
            nested = [_first_characters(alt) for alt in av[1]]
            if any(item is None for item in nested):
                return None
            return set().union(*(item for item in nested if item is not None))
        if op in _REPEAT_OPS:
            return None
        # AT (anchors) and similar zero-width ops consume nothing.
    return None


def _branch_is_ambiguous(node: Iterable[t.Any]) -> bool:
    """Return True when two alternatives can start on the same character."""
    for op, av in node:
        if op is _BRANCH:
            firsts = [_first_characters(alt) for alt in av[1]]
            if any(item is None for item in firsts):
                return True
            for i, left in enumerate(firsts):
                for right in firsts[i + 1 :]:
                    if left is not None and right is not None and left & right:
                        return True
        elif op is _SUBPATTERN:
            if _branch_is_ambiguous(av[3]):
                return True
        elif op in _REPEAT_OPS and _branch_is_ambiguous(av[2]):
            return True
    return False


def _ambiguous_repeat(node: Iterable[t.Any]) -> str | None:
    """Return why *node* can backtrack catastrophically, or ``None``.

    Two shapes, both meaning "the body can match the same input more
    than one way, so failing forces the engine to try every split":

    * a large repeat inside a large repeat -- ``(a+)+``, ``(a*)*``
    * a large repeat over alternatives that can begin with the same
      character -- ``(a|a)+``, ``(a|ab)+``. ``(cat|dog)+`` is fine,
      since no input can take both branches.
    """
    for op, av in node:
        if op in _REPEAT_OPS:
            minimum, maximum, body = av
            if _repeat_is_large(minimum, maximum):
                if _contains_large_repeat(body):
                    return "a repeated group that already contains a repeat"
                if _branch_is_ambiguous(body):
                    return "a repeated group whose alternatives overlap"
            found = _ambiguous_repeat(body)
            if found is not None:
                return found
        elif op is _SUBPATTERN:
            found = _ambiguous_repeat(av[3])
            if found is not None:
                return found
        elif op is _BRANCH:
            for branch in av[1]:
                found = _ambiguous_repeat(branch)
                if found is not None:
                    return found
        elif op is _ATOMIC_GROUP:
            found = _ambiguous_repeat(av)
            if found is not None:
                return found
    return None


def compile_pattern(
    value: str, *, regex: bool, flags: int, label: str
) -> re.Pattern[str]:
    """Compile a caller pattern, refusing one that could not be stopped.

    A literal (``regex=False``) is escaped and never checked -- it has
    no quantifiers to nest.
    """
    if not regex:
        return re.compile(re.escape(value), flags)
    try:
        compiled = re.compile(value, flags)
    except re.error as err:
        msg = f"Invalid regex pattern: {err}"
        raise ExpectedToolError(msg) from err
    try:
        parsed = _re_parser.parse(value, flags)
    except re.error:  # pragma: no cover - re.compile already accepted it
        return compiled
    reason = _ambiguous_repeat(parsed)
    if reason is not None:
        msg = (
            f"{label} pattern {value!r} contains {reason}, which can take "
            "exponential time on a non-matching line. Python's regex engine "
            "cannot be interrupted once it starts, so this is refused rather "
            "than timed out. Rewrite the repeat without nesting -- 'a+' "
            "rather than '(a+)+' -- or pass regex=false to match literally."
        )
        raise ExpectedToolError(msg)
    return compiled
