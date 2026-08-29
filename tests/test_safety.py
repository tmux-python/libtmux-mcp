"""Tests for safety tiers and their MCP annotations."""

from __future__ import annotations

from libtmux_mcp._safety import (
    ANNOTATIONS_CREATE,
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    ANNOTATIONS_SHELL,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    TAG_READONLY,
    VALID_SAFETY_LEVELS,
)

# ---------------------------------------------------------------------------
# Annotation and tag constants tests
# ---------------------------------------------------------------------------

_ANNOTATION_KEYS = {
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
}


def test_annotation_presets_have_correct_keys() -> None:
    """All annotation presets contain exactly the four MCP annotation keys."""
    for preset in (
        ANNOTATIONS_RO,
        ANNOTATIONS_MUTATING,
        ANNOTATIONS_CREATE,
        ANNOTATIONS_SHELL,
        ANNOTATIONS_DESTRUCTIVE,
    ):
        assert set(preset.keys()) == _ANNOTATION_KEYS


def test_annotations_ro_is_readonly() -> None:
    """ANNOTATIONS_RO marks tools as read-only."""
    assert ANNOTATIONS_RO["readOnlyHint"] is True
    assert ANNOTATIONS_RO["destructiveHint"] is False


def test_annotations_destructive_is_destructive() -> None:
    """ANNOTATIONS_DESTRUCTIVE marks tools as destructive."""
    assert ANNOTATIONS_DESTRUCTIVE["destructiveHint"] is True
    assert ANNOTATIONS_DESTRUCTIVE["readOnlyHint"] is False


def test_annotations_shell_is_open_world() -> None:
    """ANNOTATIONS_SHELL marks shell-driving tools as open-world.

    Shell-driving tools (``send_keys``, ``paste_text``, ``pipe_pane``)
    interact with arbitrary external state through whatever command the
    caller runs — the canonical open-world MCP interaction.
    """
    assert ANNOTATIONS_SHELL["openWorldHint"] is True
    assert ANNOTATIONS_SHELL["readOnlyHint"] is False
    assert ANNOTATIONS_SHELL["destructiveHint"] is False
    assert ANNOTATIONS_SHELL["idempotentHint"] is False


def test_annotations_create_is_closed_world() -> None:
    """ANNOTATIONS_CREATE does NOT set openWorldHint.

    Create-style mutating tools (``create_session``, ``create_window``,
    ``split_window``, ``swap_pane``, ``enter_copy_mode``) allocate tmux
    objects but do not interact with an open-ended environment. The
    shell-driving case is separately handled by ``ANNOTATIONS_SHELL``.
    """
    assert ANNOTATIONS_CREATE["openWorldHint"] is False


def test_tag_constants() -> None:
    """Safety tier tag constants are distinct strings."""
    tags = {TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE}
    assert len(tags) == 3


def test_valid_safety_levels_matches_tags() -> None:
    """VALID_SAFETY_LEVELS contains all tag constants."""
    assert {TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE} == VALID_SAFETY_LEVELS
