"""Tests for QueryList field lookups."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError
from libtmux.session import Session

from libtmux_mcp._filters import _apply_filters
from libtmux_mcp._serialize import (
    _serialize_session,
)
from libtmux_mcp.models import SessionInfo

if t.TYPE_CHECKING:
    from libtmux.server import Server


class ApplyFiltersFixture(t.NamedTuple):
    """Test fixture for _apply_filters."""

    test_id: str
    filters: dict[str, str | bool | int] | str | None
    expected_count: int | None  # None = don't check exact count
    expect_error: bool
    error_match: str | None


APPLY_FILTERS_FIXTURES: list[ApplyFiltersFixture] = [
    ApplyFiltersFixture(
        test_id="none_returns_all",
        filters=None,
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="empty_dict_returns_all",
        filters={},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="exact_match",
        filters={"session_name": "<session_name>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="no_match_returns_empty",
        filters={"session_name": "nonexistent_xyz_999"},
        expected_count=0,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="invalid_operator",
        filters={"session_name__badop": "test"},
        expected_count=None,
        expect_error=True,
        error_match="is not a filter operator",
    ),
    # A typo'd FIELD used to return [] rather than erroring, so an empty
    # result was indistinguishable from "nothing matched".
    ApplyFiltersFixture(
        test_id="unknown_field_with_valid_operator_errors",
        filters={"nosuch_field__contains": "x"},
        expected_count=None,
        expect_error=True,
        error_match="Unknown filter field 'nosuch_field'",
    ),
    ApplyFiltersFixture(
        test_id="unknown_field_without_operator_errors",
        filters={"totally_bogus": "zzz"},
        expected_count=None,
        expect_error=True,
        error_match="Unknown filter field 'totally_bogus'",
    ),
    ApplyFiltersFixture(
        test_id="near_miss_field_suggests_alternatives",
        filters={"session_nme__contains": "x"},
        expected_count=None,
        expect_error=True,
        error_match="Did you mean: session_name",
    ),
    ApplyFiltersFixture(
        test_id="nested_traversal_still_allowed",
        filters={"active_window__window_name__contains": ""},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="contains_operator",
        filters={"session_name__contains": "<partial>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="string_filter_exact",
        filters='{"session_name": "<session_name>"}',
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="string_filter_contains",
        filters='{"session_name__contains": "<partial>"}',
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="string_filter_invalid_json",
        filters="{bad json",
        expected_count=None,
        expect_error=True,
        error_match="Invalid filters JSON",
    ),
    ApplyFiltersFixture(
        test_id="string_filter_not_object",
        filters='"just a string"',
        expected_count=None,
        expect_error=True,
        error_match="filters must be a JSON object",
    ),
    ApplyFiltersFixture(
        test_id="string_filter_array",
        filters='["not", "a", "dict"]',
        expected_count=None,
        expect_error=True,
        error_match="filters must be a JSON object",
    ),
    # window_count is an output field with no tmux attribute of that
    # name; it resolves through an alias.
    ApplyFiltersFixture(
        test_id="output_field_alias",
        filters={"window_count": "1"},
        expected_count=None,
        expect_error=False,
        error_match=None,
    ),
    ApplyFiltersFixture(
        test_id="unknown_field_names_the_output_fields",
        filters={"bogus_key": "x"},
        expected_count=None,
        expect_error=True,
        error_match="Every field this tool returns is filterable",
    ),
    # A trailing segment that is not an operator is part of the path.
    ApplyFiltersFixture(
        test_id="traversal_without_trailing_operator",
        filters={"active_pane__pane_id": "<active_pane_id>"},
        expected_count=1,
        expect_error=False,
        error_match=None,
    ),
    # ...which must not let a mistyped operator read as a path and
    # filter everything out silently.
    ApplyFiltersFixture(
        test_id="mistyped_operator_still_errors",
        filters={"session_name__containss": "<partial>"},
        expected_count=None,
        expect_error=True,
        error_match="is not a filter operator",
    ),
]


@pytest.mark.parametrize(
    ApplyFiltersFixture._fields,
    APPLY_FILTERS_FIXTURES,
    ids=[f.test_id for f in APPLY_FILTERS_FIXTURES],
)
def test_apply_filters(
    mcp_server: Server,
    mcp_session: Session,
    test_id: str,
    filters: dict[str, str | bool | int] | str | None,
    expected_count: int | None,
    expect_error: bool,
    error_match: str | None,
) -> None:
    """_apply_filters bridges dict params to QueryList.filter()."""
    # Substitute placeholders with real session name
    if isinstance(filters, str):
        session_name = mcp_session.session_name
        assert session_name is not None
        filters = filters.replace("<session_name>", session_name)
        filters = filters.replace("<partial>", session_name[:4])
    elif filters is not None:
        session_name = mcp_session.session_name
        assert session_name is not None
        resolved: dict[str, str | bool | int] = {}
        for k, v in filters.items():
            if v == "<session_name>":
                resolved[k] = session_name
            elif v == "<partial>":
                resolved[k] = session_name[:4]
            elif v == "<active_pane_id>":
                active_pane = mcp_session.active_window.active_pane
                assert active_pane is not None
                assert active_pane.pane_id is not None
                resolved[k] = active_pane.pane_id
            else:
                resolved[k] = v
        filters = resolved

    sessions = mcp_server.sessions

    if expect_error:
        with pytest.raises(ToolError, match=error_match):
            _apply_filters(sessions, filters, _serialize_session, Session, SessionInfo)
    else:
        result = _apply_filters(
            sessions, filters, _serialize_session, Session, SessionInfo
        )
        assert isinstance(result, list)
        if expected_count is not None:
            assert len(result) == expected_count
        else:
            assert len(result) >= 1
