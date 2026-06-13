"""Tests for package metadata files."""

from __future__ import annotations

import pathlib

import libtmux_mcp


def test_package_contains_py_typed_marker() -> None:
    """The installed package advertises inline typing via ``py.typed``."""
    package_dir = pathlib.Path(libtmux_mcp.__file__).parent

    assert (package_dir / "py.typed").is_file()
