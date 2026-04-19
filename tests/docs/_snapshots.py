"""Snapshot helpers for HTML fragment assertions.

Mirrors the pattern from ``gp-sphinx/tests/_snapshots.py`` — a thin normaliser
plus a ``snapshot_html_fragment`` fixture that wraps syrupy's ``snapshot``
assertion. Keep this file dependency-light: no Sphinx imports.
"""

from __future__ import annotations

import pathlib
import typing as t

import pytest

if t.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _replace_roots(text: str, roots: tuple[pathlib.Path, ...]) -> str:
    """Replace concrete filesystem roots with stable placeholders."""
    normalized = text
    for index, root in enumerate(roots, start=1):
        normalized = normalized.replace(str(root), f"<root-{index}>")
    return normalized


def normalize_html_fragment(
    fragment: str,
    *,
    roots: tuple[pathlib.Path, ...] = (),
) -> str:
    """Return a stable HTML fragment string for snapshot assertions."""
    normalized = fragment.strip().replace("\r\n", "\n")
    return _replace_roots(normalized, roots)


class _HTMLFragmentSnapshot(t.Protocol):
    """Callable signature for the ``snapshot_html_fragment`` fixture."""

    def __call__(
        self,
        fragment: str,
        *,
        name: str | None = None,
        roots: tuple[pathlib.Path, ...] = (),
    ) -> None: ...


@pytest.fixture
def snapshot_html_fragment(snapshot: SnapshotAssertion) -> _HTMLFragmentSnapshot:
    """Assert a normalized HTML fragment snapshot (see ``gp-sphinx`` pattern)."""
    base_snapshot = snapshot.with_defaults()

    def _assert(
        fragment: str,
        *,
        name: str | None = None,
        roots: tuple[pathlib.Path, ...] = (),
    ) -> None:
        expected = base_snapshot(name=name) if name is not None else base_snapshot
        assert normalize_html_fragment(fragment, roots=roots) == expected

    return _assert
