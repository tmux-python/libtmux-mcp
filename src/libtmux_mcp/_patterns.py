r"""Caller-supplied regex compilation with a backtracking bound.

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
worker cap can reclaim a thread stuck inside ``re``. A repeat that can
iterate freely is refused when its body can match one string more than
one way -- a body of varying width (``(a+)+``, ``(a{0,3})*``,
``(a?){20}``), or alternatives that can begin on the same character
(``(a|a)+``). ``(cat|dog)+`` and ``(\d{2}){3}`` are fixed-width and
stay.

That is a MODEL of catastrophic backtracking, not a proof of its
absence, and it is deliberately coarser than the engine: ``(a?)*`` is
refused although CPython prunes it, because a screen that leaned on
that pruning would also have to know when it does not apply.
"""

from __future__ import annotations

import re
import typing as t

from libtmux_mcp._errors import ExpectedToolError

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
_ASSERT = _re_constants.ASSERT
_ASSERT_NOT = _re_constants.ASSERT_NOT
_GROUPREF_EXISTS = _re_constants.GROUPREF_EXISTS
_LITERAL = _re_constants.LITERAL

#: Ops that match without consuming input, so they contribute no first
#: character of their own.
_ZERO_WIDTH = frozenset({_re_constants.AT, _ASSERT, _ASSERT_NOT})

#: A repeat this large is treated as unbounded for nesting purposes.
#: ``(\d{2}){3}`` is six characters of work; ``(a{1,50}){1,50}`` is a
#: bomb with a number in front of it.
_LARGE_REPEAT = 20

#: Ops that consume exactly one character. Everything else either
#: consumes nothing, nests, or is unmodelled -- and an unmodelled op is
#: given an unknown width, which only ever refuses more.
_ONE_CHARACTER = frozenset(
    {
        _LITERAL,
        _re_constants.NOT_LITERAL,
        _re_constants.IN,
        _re_constants.ANY,
    }
)


def _repeat_is_large(minimum: int, maximum: int) -> bool:
    """Return True when a repeat can iterate enough times to matter."""
    return maximum is _MAXREPEAT or maximum >= _LARGE_REPEAT or maximum > minimum > 0


def _subpatterns(op: t.Any, av: t.Any) -> list[Iterable[t.Any]]:
    """Every pattern sequence nested inside one parsed node.

    Each walker below recurses through this, so a container op is taught
    to the screen once instead of once per walker. An op missing here is
    a repeat the screen cannot see.
    """
    if op is _SUBPATTERN:
        return [av[3]]
    if op is _BRANCH:
        return list(av[1])
    if op in _REPEAT_OPS:
        return [av[2]]
    if op is _ATOMIC_GROUP:
        return [av]
    if op is _ASSERT or op is _ASSERT_NOT:
        return [av[1]]
    if op is _GROUPREF_EXISTS:
        return [branch for branch in av[1:] if branch]
    return []


def _width_range(node: Iterable[t.Any]) -> tuple[int, int | None]:
    """How many characters *node* can match, as ``(minimum, maximum)``.

    ``None`` as the maximum means unbounded. A node whose two ends
    differ can match one string in more than one way, and that is what
    an enclosing repeat has to backtrack through.
    """
    low, high = 0, t.cast("int | None", 0)
    for op, av in node:
        if op in _ZERO_WIDTH:
            continue
        if op in _ONE_CHARACTER:
            lo, hi = 1, t.cast("int | None", 1)
        elif op in _REPEAT_OPS:
            body_lo, body_hi = _width_range(av[2])
            lo = av[0] * body_lo
            hi = None if body_hi is None or av[1] is _MAXREPEAT else av[1] * body_hi
        else:
            children = _subpatterns(op, av)
            if not children:
                # A backreference, or an op this screen does not model.
                lo, hi = 0, None
            else:
                spans = [_width_range(child) for child in children]
                lo = min(span[0] for span in spans)
                hi = (
                    None
                    if any(span[1] is None for span in spans)
                    else max(t.cast("int", span[1]) for span in spans)
                )
        low += lo
        high = None if high is None or hi is None else high + hi
    return low, high


def _is_variable_width(node: Iterable[t.Any]) -> bool:
    """Whether *node* can match differing numbers of characters."""
    low, high = _width_range(node)
    return high is None or high != low


def _first_characters(branch: Iterable[t.Any]) -> set[t.Any] | None:
    """Characters a branch can start with, or ``None`` if unbounded.

    ``None`` means "assume it overlaps" and covers an EMPTY branch,
    which is the worst case: a repeat whose body matches nothing is
    ambiguous at every position. Any op this screen does not model
    lands there too.
    """
    for op, av in branch:
        if op is _LITERAL:
            return {av}
        if op is _SUBPATTERN:
            return _first_characters(av[3])
        if op is _BRANCH:
            nested = [_first_characters(alt) for alt in av[1]]
            if any(item is None for item in nested):
                return None
            return set().union(*(item for item in nested if item is not None))
        if op in _ZERO_WIDTH:
            continue
        return None
    return None


def _branch_is_ambiguous(node: Iterable[t.Any]) -> bool:
    """Return True when two alternatives can start on the same character."""
    for op, av in node:
        if op is _BRANCH:
            firsts = [_first_characters(alt) for alt in av[1]]
            if any(item is None for item in firsts):
                return True
            known = [item for item in firsts if item is not None]
            for i, left in enumerate(known):
                if any(left & right for right in known[i + 1 :]):
                    return True
        if any(_branch_is_ambiguous(child) for child in _subpatterns(op, av)):
            return True
    return False


def _ambiguous_repeat(node: Iterable[t.Any]) -> str | None:
    """Return why *node* can backtrack catastrophically, or ``None``.

    Two shapes, both meaning "the body can match the same input more
    than one way, so failing forces the engine to try every split":

    * a body of varying width -- ``(a+)+``, ``(a{0,3})*``, ``(a?){20}``.
      A nested repeat is the common case, but the predicate is the
      width, which also catches ``(a?a?){1,20}``.
    * alternatives that can begin with the same character --
      ``(a|a)+``. ``(cat|dog)+`` is fine, since no input takes both.
    """
    for op, av in node:
        if op in _REPEAT_OPS and _repeat_is_large(av[0], av[1]):
            if _is_variable_width(av[2]):
                return "a repeated group whose body can match a varying width"
            if _branch_is_ambiguous(av[2]):
                return "a repeated group whose alternatives overlap"
        for child in _subpatterns(op, av):
            found = _ambiguous_repeat(child)
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
