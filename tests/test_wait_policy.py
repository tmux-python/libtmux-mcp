"""Tests for the wait-tool duration policy."""

from __future__ import annotations

import logging

import pytest

from libtmux_mcp._wait_policy import (
    WAIT_MAX_SECONDS_DEFAULT,
    WAIT_MAX_SECONDS_FLOOR,
    WAIT_MAX_SECONDS_LIMIT,
    _configure_wait_ceiling,
    _resolve_wait_max_seconds,
    _wait_ceiling_seconds,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, WAIT_MAX_SECONDS_DEFAULT),
        ("45", 45.0),
        ("1.5", 1.5),
        # Clamped to the hard bounds on the override itself.
        ("0.1", WAIT_MAX_SECONDS_FLOOR),
        ("0", WAIT_MAX_SECONDS_FLOOR),
        ("-5", WAIT_MAX_SECONDS_FLOOR),
        ("100000", WAIT_MAX_SECONDS_LIMIT),
        # Never raises: a bad value degrades to the default.
        ("banana", WAIT_MAX_SECONDS_DEFAULT),
        ("", WAIT_MAX_SECONDS_DEFAULT),
        ("inf", WAIT_MAX_SECONDS_DEFAULT),
        ("nan", WAIT_MAX_SECONDS_DEFAULT),
    ],
    ids=[
        "unset",
        "in-range",
        "in-range-fractional",
        "below-floor",
        "zero",
        "negative",
        "above-limit",
        "not-a-number",
        "empty",
        "infinity",
        "nan",
    ],
)
def test_resolve_wait_max_seconds(raw: str | None, expected: float) -> None:
    """Env resolution clamps and degrades instead of raising.

    Mirrors ``_resolve_safety_level``: an operator typo must not stop
    the server from starting, and an out-of-range value must not
    silently become an unbounded wait.
    """
    assert _resolve_wait_max_seconds(raw) == expected


def test_resolve_wait_max_seconds_warns_on_bad_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rejected value is logged so operators can see the fallback."""
    with caplog.at_level(logging.WARNING, logger="libtmux_mcp._wait_policy"):
        _resolve_wait_max_seconds("banana")
    assert "LIBTMUX_MCP_WAIT_MAX_SECONDS" in caplog.text


def test_configure_wait_ceiling_reclamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The publish path re-clamps so no programmatic caller can unbound it."""
    from libtmux_mcp import _wait_policy

    monkeypatch.setattr(_wait_policy, "_wait_max_seconds", WAIT_MAX_SECONDS_DEFAULT)

    _configure_wait_ceiling(10_000.0)
    assert _wait_ceiling_seconds() == WAIT_MAX_SECONDS_LIMIT

    _configure_wait_ceiling(0.0)
    assert _wait_ceiling_seconds() == WAIT_MAX_SECONDS_FLOOR


def test_default_ceiling_is_30_seconds() -> None:
    """The shipped default ceiling is 30 s."""
    assert WAIT_MAX_SECONDS_DEFAULT == 30.0
    assert (WAIT_MAX_SECONDS_FLOOR, WAIT_MAX_SECONDS_LIMIT) == (1.0, 120.0)
