"""Tests for package metadata files."""

from __future__ import annotations

import ast
import graphlib
import pathlib
import typing as t

import pytest

import libtmux_mcp


def test_package_contains_py_typed_marker() -> None:
    """The installed package advertises inline typing via ``py.typed``."""
    package_dir = pathlib.Path(libtmux_mcp.__file__).parent

    assert (package_dir / "py.typed").is_file()


def _load_toml(path: pathlib.Path) -> dict[str, t.Any]:
    """Parse ``path`` as TOML.

    Imported lazily: ``tomllib`` is stdlib only from 3.11, and this
    project declares ``requires-python = ">=3.10"``. CI runs 3.14, so
    the guards below always execute there.
    """
    tomllib = pytest.importorskip("tomllib", reason="stdlib TOML needs 3.11+")
    parsed: dict[str, t.Any] = tomllib.loads(path.read_text())
    return parsed


def _repo_root() -> pathlib.Path:
    """Directory holding ``pyproject.toml``, or skip if running installed."""
    for candidate in pathlib.Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    pytest.skip("not running from a source checkout")


def test_no_local_path_dependency_is_committed() -> None:
    """Dependencies must resolve the same way here and in CI.

    A ``[tool.uv.sources]`` path entry pointing at a sibling checkout is
    a useful local aid -- it is how this server gets exercised against
    unreleased libtmux -- and it is not a local one once committed. CI
    runs ``uv sync --all-extras --dev``, which reads the same table, so
    a relative path fails dependency resolution before a single test
    runs, and so does any worktree or clone that is not the one
    directory the path was written against. Measured: a peer's worktree
    could not resolve at all.

    A comment saying "drop this before a PR" is the shape this project
    keeps finding bugs in -- an invariant held by convention rather than
    by construction. This asserts it. If it fails, a local pin is in
    place, which is exactly what a caller needs to be told.
    """
    pyproject = _load_toml(_repo_root() / "pyproject.toml")
    sources = pyproject.get("tool", {}).get("uv", {}).get("sources", {})
    pinned = {name: spec for name, spec in sources.items() if "path" in spec}

    assert not pinned, (
        f"local path source(s) committed: {pinned}. These resolve only in "
        "the one directory they were written against -- CI's `uv sync` and "
        "every worktree fail on them."
    )


def test_the_lockfile_has_no_foreign_editable() -> None:
    """Removing the pyproject stanza alone does not undo a local pin.

    ``uv.lock`` records the resolved source separately, so a lock left
    behind keeps pointing at the sibling checkout even after the table
    is gone. Only the project itself may be editable.
    """
    lock = _load_toml(_repo_root() / "uv.lock")
    foreign = sorted(
        package["name"]
        for package in lock.get("package", [])
        if package.get("source", {}).get("editable", ".") != "."
    )

    assert not foreign, f"lockfile pins editable source(s) outside the repo: {foreign}"


def test_core_modules_never_import_a_tool_module() -> None:
    """Core may not depend on ``tools/``; only the other direction is legal.

    A core module reaching into a tool package forms a cycle that a
    function-level import hides at no cost to any linter or type
    checker, so nothing else in the gate chain reports it.
    """
    core = pathlib.Path(libtmux_mcp.__file__).parent
    offenders = sorted(
        f"{path.name}:{node.lineno} imports {node.module}"
        for path in core.glob("_*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("libtmux_mcp.tools")
    )

    assert not offenders, f"core module depends on a tool module: {offenders}"


def test_core_modules_form_an_acyclic_import_graph() -> None:
    """Core modules must stay a DAG.

    The workaround for a cycle is a function-level import, which costs
    no linter or type checker anything, so the cycle then survives every
    gate in the chain and is visible only to whoever next tries to hoist
    it.
    """
    core = pathlib.Path(libtmux_mcp.__file__).parent
    graph = {
        path.stem: {
            (node.module or "").split(".")[1]
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("libtmux_mcp._")
        }
        for path in core.glob("_*.py")
    }

    try:
        graphlib.TopologicalSorter(graph).prepare()
    except graphlib.CycleError as err:
        pytest.fail(f"import cycle among core modules: {' -> '.join(err.args[1])}")
