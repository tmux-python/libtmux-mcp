"""Django-style field lookups over libtmux QueryLists.

tmux reports every field as text, so a filter value is coerced to the
shape tmux would have reported before it is compared.
"""

from __future__ import annotations

import dataclasses
import difflib
import functools
import json
import logging
import typing as t

from libtmux._internal.query_list import LOOKUP_NAME_MAP, QueryList

if t.TYPE_CHECKING:
    from pydantic import BaseModel


from libtmux_mcp._errors import ExpectedToolError

logger = logging.getLogger(__name__)


M = t.TypeVar("M")


def _coerce_dict_arg(
    name: str,
    value: dict[str, t.Any] | str | None,
) -> dict[str, t.Any] | None:
    """Coerce a tool parameter to a dict, accepting JSON-string form.

    Workaround: Cursor's composer-1/composer-1.5 models and some other
    MCP clients serialize dict params as JSON strings instead of
    objects. Claude and GPT models through Cursor work fine; the bug
    is model-specific. This helper is the canonical place to absorb
    the string form so each tool can stay dict-typed on the Python
    side. Callers pass ``name`` so the error messages identify the
    offending parameter.

    See:
        https://forum.cursor.com/t/145807
        https://github.com/anthropics/claude-code/issues/5504

    Parameters
    ----------
    name : str
        Parameter name, used in error messages.
    value : dict, str, or None
        Either an already-decoded dict, a JSON string of a dict, or
        ``None``.

    Returns
    -------
    dict or None
        The decoded dict, or ``None`` if the input was ``None`` or an
        empty string.

    Raises
    ------
    ExpectedToolError
        If ``value`` is a string that is not valid JSON, or decodes to
        a JSON value that is not an object.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, ValueError) as e:
            msg = f"Invalid {name} JSON: {e}"
            raise ExpectedToolError(msg) from e
        if not isinstance(decoded, dict):
            msg = f"{name} must be a JSON object, got {type(decoded).__name__}"
            raise ExpectedToolError(msg) from None
        return decoded
    return value


@functools.cache
def _filterable_fields(obj_type: type) -> frozenset[str]:
    """Attribute names a filter key may begin with.

    ``QueryList`` resolves a key by ``getattr`` traversal and treats a
    miss as "no match", so an unknown field silently filters every row
    out and an empty result is indistinguishable from a typo.

    Deliberately permissive: it rejects names the type cannot have and
    accepts everything else, because ``__`` traversal into a nested
    object is legitimate and only the first segment is checkable here.
    """
    names = {name for name in dir(obj_type) if not name.startswith("_")}
    if dataclasses.is_dataclass(obj_type):
        names |= {field.name for field in dataclasses.fields(obj_type)}
    return frozenset(names)


_MODEL_FIELD_ALIASES: dict[str, str] = {
    "window_count": "session_windows",
    "pane_count": "window_panes",
    "active_pane_id": "active_pane__pane_id",
}
"""Output fields tmux exposes under a different attribute name."""


def _admits_bool(annotation: t.Any) -> bool:
    """Whether a model field's annotation can hold a bool."""
    return annotation is bool or bool in t.get_args(annotation)


_BOOL_TRUE = frozenset({"true", "1", "yes"})
_BOOL_FALSE = frozenset({"false", "0", "no"})

#: Operators that mean anything against a bool. The rest are string or
#: collection tests, and libtmux's lookups fall through to ``False`` for
#: a bool -- so allowing them answers every query with an empty list,
#: contradictory pairs like ``__in``/``__nin`` included.
_BOOL_OPERATORS = frozenset({"exact", "eq"})


def _coerce_model_value(key: str, value: t.Any, annotation: t.Any) -> t.Any:
    """Coerce a filter value to what the model field actually holds.

    ``filters`` is typed ``dict[str, str]``, so a bool field is
    addressed as ``"true"``; comparing that to ``True`` never matches.
    An unrecognised token is rejected rather than compared as a string,
    which would report "nothing matched" for a typo.
    """
    if isinstance(value, str) and _admits_bool(annotation):
        lowered = value.strip().lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        msg = (
            f"Filter '{key}' takes a boolean, got {value!r}. Use one of: "
            f"{', '.join(sorted(_BOOL_TRUE | _BOOL_FALSE))}."
        )
        raise ExpectedToolError(msg)
    return value


def _path_resolves(item: t.Any, path: str) -> bool:
    """Whether ``path``'s ``__``-separated segments resolve on ``item``.

    ``None`` ends the walk only at an INTERMEDIATE segment, where there
    is genuinely nothing to traverse into. On the terminal segment it is
    an ordinary value -- tmux leaves many format fields empty, so
    ``active_pane__pane_start_command`` is None on every shell pane --
    and treating that as unresolvable turns a true empty result into a
    false error.
    """
    current = item
    segments = path.split("__")
    last = len(segments) - 1
    for i, segment in enumerate(segments):
        try:
            current = getattr(current, segment)
        except Exception:  # noqa: BLE001 - any failure means "no such path"
            return False
        if current is None:
            return i == last
    return True


def _attribute_access_error(probe: list[t.Any], field: str) -> str | None:
    """Message if ``field`` raises on every probed item, else ``None``.

    libtmux keeps removed properties around so they raise a message
    naming the replacement. ``dir()`` still lists them, so they reach
    callers as filterable; ``QueryList`` swallows the raise and answers
    an empty list. Surfacing libtmux's own message is what makes the
    refusal useful.

    One item settles it: the raise comes from the class, so it cannot
    differ per instance.
    """
    if not probe:
        return None
    try:
        getattr(probe[0], field)
    except Exception as exc:  # noqa: BLE001 - reported, not handled
        return str(exc)
    return None


def _unknown_field_message(
    key: str,
    field: str,
    allowed_fields: frozenset[str],
    model_fields: t.Mapping[str, t.Any],
    obj_type: type,
) -> str:
    """Build the error for a filter key naming no known field."""
    msg = f"Unknown filter field '{field}' in '{key}'."
    known = sorted(set(allowed_fields) | set(model_fields))
    close = difflib.get_close_matches(field, known, n=3)
    if close:
        msg += f" Did you mean: {', '.join(close)}?"
    return (
        f"{msg} Every field this tool returns is filterable: "
        f"{', '.join(sorted(model_fields))}. libtmux "
        f"{obj_type.__name__} attributes are accepted too, though tmux "
        "leaves many of them empty."
    )


def _raise_if_path_unresolvable(
    probe: list[t.Any],
    field_path: str,
    key: str,
    valid_ops: list[str],
    *,
    operator_parsed: bool,
) -> None:
    """Reject a multi-segment path no item can resolve.

    Guards the traversal fallback: without this, a mistyped operator
    (``session_name__containss``) reads as a path, resolves on nothing
    and filters every row out -- the silent-empty answer this module
    exists to prevent. Only provable when something is there to probe,
    so an empty list is left alone.
    """
    if "__" not in field_path:
        return
    if not probe or any(_path_resolves(item, field_path) for item in probe):
        return
    msg = f"Filter '{key}' names no attribute path on any item."
    if not operator_parsed:
        # Only a key with no operator can be a mistyped one. When an
        # operator WAS parsed off, blaming the last path segment for
        # not being one denies the operator the caller supplied.
        trailing = field_path.rsplit("__", 1)[1]
        close = difflib.get_close_matches(trailing, valid_ops, n=3)
        msg += (
            f" '{trailing}' is not a filter operator either; did you mean '{close[0]}'?"
            if close
            else f" '{trailing}' is not a filter operator either."
        )
    raise ExpectedToolError(msg)


def _as_tmux_text(value: str | bool | int) -> str | bool | int:
    """Render a typed filter value the way tmux reports the field.

    tmux-derived attributes are always STRINGS -- ``pane_width`` is
    ``"80"``, ``pane_active`` is ``"1"``. Comparing them against a real
    ``80`` or ``True`` matches nothing, so accepting typed values in the
    schema without this would trade a validation error for a confident
    empty result. Booleans first: ``bool`` is a subclass of ``int``.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return value


def _apply_filters(
    items: t.Any,
    filters: dict[str, str | bool | int] | str | None,
    serializer: t.Callable[..., M],
    obj_type: type,
    model_type: type[BaseModel],
) -> list[M]:
    """Apply QueryList filters and serialize results.

    Parameters
    ----------
    items : QueryList
        The QueryList of tmux objects to filter.
    filters : dict or str, optional
        Django-style filters as a dict (e.g. ``{"session_name__contains": "dev"}``)
        or as a JSON string. Some MCP clients require the string form.
        If None or empty, all items are returned.
    serializer : callable
        Serializer function to convert each item to a model.
    obj_type : type
        libtmux class of the filtered items, used to validate filter
        field names. Taken as a parameter rather than read off the
        first item so an empty list still validates -- an empty result
        is exactly when a typo most needs reporting.
    model_type : type
        Model ``serializer`` returns. Its fields are filterable too, so
        that filtering by what a listing displayed always works.

    Returns
    -------
    list
        Serialized list of matching items.

    Raises
    ------
    ExpectedToolError
        If a filter key uses an invalid lookup operator or names a
        field the object cannot have.
    """
    coerced = _coerce_dict_arg("filters", filters)
    if not coerced:
        return [serializer(item) for item in items]
    filters = coerced

    valid_ops = sorted(LOOKUP_NAME_MAP.keys())
    allowed_fields = _filterable_fields(obj_type)
    model_fields = model_type.model_fields
    attr_filters: dict[str, t.Any] = {}
    model_filters: dict[str, t.Any] = {}
    probe = list(items)

    for key, value in filters.items():
        # Matching QueryList: an unknown trailing segment is part of the
        # attribute path with the operator defaulting to ``exact``, so
        # ``active_pane__pane_id`` traverses.
        field_path, op = key, ""
        if "__" in key:
            lhs, trailing = key.rsplit("__", 1)
            if trailing in LOOKUP_NAME_MAP:
                field_path, op = lhs, trailing

        field = field_path.split("__", 1)[0]
        if field in allowed_fields:
            removed = _attribute_access_error(probe, field)
            if removed is not None:
                msg = f"Filter field '{field}' cannot be read: {removed}"
                raise ExpectedToolError(msg)
            _raise_if_path_unresolvable(
                probe, field_path, key, valid_ops, operator_parsed=bool(op)
            )
            attr_filters[key] = _as_tmux_text(value)
        elif field in _MODEL_FIELD_ALIASES:
            attr_filters[_MODEL_FIELD_ALIASES[field] + key[len(field) :]] = (
                _as_tmux_text(value)
            )
        elif field in model_fields:
            annotation = model_fields[field].annotation
            if _admits_bool(annotation) and op and op not in _BOOL_OPERATORS:
                msg = (
                    f"Operator '{op}' does not apply to boolean field "
                    f"'{field}'. Use {' or '.join(sorted(_BOOL_OPERATORS))}, "
                    "or omit the operator."
                )
                raise ExpectedToolError(msg)
            # Computed server-side, so it exists only after serializing.
            model_filters[key] = _coerce_model_value(key, value, annotation)
        else:
            raise ExpectedToolError(
                _unknown_field_message(
                    key, field, allowed_fields, model_fields, obj_type
                )
            )

    filtered = items.filter(**attr_filters) if attr_filters else items
    results = [serializer(item) for item in filtered]
    if model_filters:
        results = list(QueryList(results).filter(**model_filters))
    return results
