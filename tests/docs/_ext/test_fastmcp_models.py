"""Tests for fastmcp_autodoc model collection and nodes."""

from __future__ import annotations

import typing as t
from unittest.mock import MagicMock

import fastmcp_autodoc
import pytest

# ---------------------------------------------------------------------------
# _collect_models
# ---------------------------------------------------------------------------


def _collect_models_result() -> dict[str, fastmcp_autodoc.ModelInfo]:
    """Run _collect_models and return the result dict."""
    app = MagicMock()
    app.env = MagicMock()
    fastmcp_autodoc._collect_models(app)
    result: dict[str, fastmcp_autodoc.ModelInfo] = app.env.fastmcp_models
    return result


def test_collect_models_discovers_all_12() -> None:
    """_collect_models finds all 12 Pydantic models in libtmux_mcp.models."""
    models = _collect_models_result()
    assert len(models) == 12


class ModelNameFixture(t.NamedTuple):
    """Test fixture for verifying model names."""

    test_id: str
    name: str


MODEL_NAME_FIXTURES: list[ModelNameFixture] = [
    ModelNameFixture(test_id="SessionInfo", name="SessionInfo"),
    ModelNameFixture(test_id="WindowInfo", name="WindowInfo"),
    ModelNameFixture(test_id="PaneInfo", name="PaneInfo"),
    ModelNameFixture(test_id="PaneContentMatch", name="PaneContentMatch"),
    ModelNameFixture(test_id="ServerInfo", name="ServerInfo"),
    ModelNameFixture(test_id="OptionResult", name="OptionResult"),
    ModelNameFixture(test_id="OptionSetResult", name="OptionSetResult"),
    ModelNameFixture(test_id="EnvironmentResult", name="EnvironmentResult"),
    ModelNameFixture(test_id="EnvironmentSetResult", name="EnvironmentSetResult"),
    ModelNameFixture(test_id="WaitForTextResult", name="WaitForTextResult"),
    ModelNameFixture(test_id="PaneSnapshot", name="PaneSnapshot"),
    ModelNameFixture(test_id="ContentChangeResult", name="ContentChangeResult"),
]


@pytest.mark.parametrize(
    MODEL_NAME_FIXTURES[0]._fields,
    MODEL_NAME_FIXTURES,
    ids=[f.test_id for f in MODEL_NAME_FIXTURES],
)
def test_collect_models_includes_model(test_id: str, name: str) -> None:
    """_collect_models includes each expected model."""
    models = _collect_models_result()
    assert name in models


# ---------------------------------------------------------------------------
# Field counts
# ---------------------------------------------------------------------------


class FieldCountFixture(t.NamedTuple):
    """Test fixture for field count verification."""

    test_id: str
    model_name: str
    expected_count: int


FIELD_COUNT_FIXTURES: list[FieldCountFixture] = [
    FieldCountFixture(
        test_id="SessionInfo_5",
        model_name="SessionInfo",
        expected_count=5,
    ),
    FieldCountFixture(
        test_id="PaneSnapshot_14",
        model_name="PaneSnapshot",
        expected_count=14,
    ),
    FieldCountFixture(
        test_id="WindowInfo_10",
        model_name="WindowInfo",
        expected_count=10,
    ),
    FieldCountFixture(
        test_id="PaneInfo_12",
        model_name="PaneInfo",
        expected_count=12,
    ),
    FieldCountFixture(
        test_id="ContentChangeResult_4",
        model_name="ContentChangeResult",
        expected_count=4,
    ),
    FieldCountFixture(
        test_id="WaitForTextResult_5",
        model_name="WaitForTextResult",
        expected_count=5,
    ),
]


@pytest.mark.parametrize(
    FIELD_COUNT_FIXTURES[0]._fields,
    FIELD_COUNT_FIXTURES,
    ids=[f.test_id for f in FIELD_COUNT_FIXTURES],
)
def test_model_field_count(
    test_id: str,
    model_name: str,
    expected_count: int,
) -> None:
    """Models have the expected number of fields."""
    models = _collect_models_result()
    model = models[model_name]
    assert len(model.fields) == expected_count


# ---------------------------------------------------------------------------
# Field description extraction
# ---------------------------------------------------------------------------


def test_field_description_extraction() -> None:
    """Field(description=...) values are extracted correctly."""
    models = _collect_models_result()
    session = models["SessionInfo"]
    field_map = {f.name: f for f in session.fields}

    assert "session_id" in field_map
    assert field_map["session_id"].description == "Session ID (e.g. '$1')"

    assert "window_count" in field_map
    assert field_map["window_count"].description == "Number of windows"


# ---------------------------------------------------------------------------
# Required vs optional detection
# ---------------------------------------------------------------------------


def test_required_vs_optional_detection() -> None:
    """Required fields and optional fields are distinguished correctly."""
    models = _collect_models_result()
    session = models["SessionInfo"]
    field_map = {f.name: f for f in session.fields}

    # session_id has no default → required
    assert field_map["session_id"].required is True
    assert field_map["session_id"].default == ""

    # session_name has default=None → optional
    assert field_map["session_name"].required is False
    assert field_map["session_name"].default == "None"

    # window_count has no default → required
    assert field_map["window_count"].required is True


# ---------------------------------------------------------------------------
# default_factory handling
# ---------------------------------------------------------------------------


def test_default_factory_handling() -> None:
    """WaitForTextResult.matched_lines uses default_factory=list."""
    models = _collect_models_result()
    wait_result = models["WaitForTextResult"]
    field_map = {f.name: f for f in wait_result.fields}

    matched_lines = field_map["matched_lines"]
    assert matched_lines.required is False
    assert matched_lines.default == "list()"


# ---------------------------------------------------------------------------
# _model_badge_node
# ---------------------------------------------------------------------------


def test_model_badge_classes() -> None:
    """_model_badge creates badge node with correct CSS classes."""
    badge = fastmcp_autodoc._model_badge()
    assert isinstance(badge, fastmcp_autodoc._model_badge_node)
    assert "sd-bg-primary" in badge["classes"]
    assert "sd-bg-text-primary" in badge["classes"]
    assert "sd-sphinx-override" in badge["classes"]
    assert "sd-badge" in badge["classes"]
    assert badge.astext() == "model"


# ---------------------------------------------------------------------------
# Model roles
# ---------------------------------------------------------------------------


def test_model_role_creates_placeholder() -> None:
    """_model_role creates _model_ref_placeholder with show_badge=True."""
    result_nodes, _messages = fastmcp_autodoc._model_role(
        "model", ":model:`SessionInfo`", "SessionInfo", 1, None
    )
    assert len(result_nodes) == 1
    node = result_nodes[0]
    assert isinstance(node, fastmcp_autodoc._model_ref_placeholder)
    assert node["reftarget"] == "SessionInfo"
    assert node["show_badge"] is True


def test_modelref_role_creates_placeholder() -> None:
    """_modelref_role creates _model_ref_placeholder with show_badge=False."""
    result_nodes, _messages = fastmcp_autodoc._modelref_role(
        "modelref", ":modelref:`SessionInfo`", "SessionInfo", 1, None
    )
    assert len(result_nodes) == 1
    node = result_nodes[0]
    assert isinstance(node, fastmcp_autodoc._model_ref_placeholder)
    assert node["reftarget"] == "SessionInfo"
    assert node["show_badge"] is False


# ---------------------------------------------------------------------------
# :fields: and :exclude: filtering
# ---------------------------------------------------------------------------


def test_model_directive_fields_allowlist() -> None:
    """FastMCPModelDirective :fields: option filters to allowed fields."""
    directive = fastmcp_autodoc.FastMCPModelDirective.__new__(
        fastmcp_autodoc.FastMCPModelDirective
    )
    directive.options = {"fields": "session_id, window_count"}

    all_fields = [
        fastmcp_autodoc.ModelFieldInfo("session_id", "str", True, "", ""),
        fastmcp_autodoc.ModelFieldInfo("session_name", "str | None", False, "None", ""),
        fastmcp_autodoc.ModelFieldInfo("window_count", "int", True, "", ""),
    ]

    filtered = directive._filter_fields(all_fields)
    names = [f.name for f in filtered]
    assert names == ["session_id", "window_count"]


def test_model_directive_exclude_denylist() -> None:
    """FastMCPModelDirective :exclude: option removes denied fields."""
    directive = fastmcp_autodoc.FastMCPModelDirective.__new__(
        fastmcp_autodoc.FastMCPModelDirective
    )
    directive.options = {"exclude": "session_name"}

    all_fields = [
        fastmcp_autodoc.ModelFieldInfo("session_id", "str", True, "", ""),
        fastmcp_autodoc.ModelFieldInfo("session_name", "str | None", False, "None", ""),
        fastmcp_autodoc.ModelFieldInfo("window_count", "int", True, "", ""),
    ]

    filtered = directive._filter_fields(all_fields)
    names = [f.name for f in filtered]
    assert names == ["session_id", "window_count"]


# ---------------------------------------------------------------------------
# Qualified names
# ---------------------------------------------------------------------------


def test_model_qualified_name() -> None:
    """Model qualified_name includes full module path."""
    models = _collect_models_result()
    assert models["SessionInfo"].qualified_name == "libtmux_mcp.models.SessionInfo"
    assert models["PaneSnapshot"].qualified_name == "libtmux_mcp.models.PaneSnapshot"
