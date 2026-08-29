"""Tests for tmux argv and the bounded command."""

from __future__ import annotations

import typing as t

import pytest

from tests.conftest import FakeServer

if t.TYPE_CHECKING:
    from libtmux.server import Server


@pytest.mark.parametrize(
    ("server", "args", "expected"),
    [
        (
            FakeServer(socket_name="s", socket_path=None),
            ("list-sessions",),
            ["tmux", "-L", "s", "list-sessions"],
        ),
        (
            FakeServer(socket_name=None, socket_path="/tmp/tmux-1000/default"),
            ("ls",),
            ["tmux", "-S", "/tmp/tmux-1000/default", "ls"],
        ),
        (
            FakeServer(socket_name="s", socket_path="/tmp/tmux-1000/s"),
            ("wait-for", "-S", "ch"),
            ["tmux", "-L", "s", "-S", "/tmp/tmux-1000/s", "wait-for", "-S", "ch"],
        ),
        (
            FakeServer(socket_name=None, socket_path=None, tmux_bin="/opt/tmux"),
            ("show-options",),
            ["/opt/tmux", "show-options"],
        ),
    ],
)
def test_tmux_argv_honours_socket_and_binary(
    server: FakeServer, args: tuple[str, ...], expected: list[str]
) -> None:
    """``_tmux_argv`` covers the socket_name / socket_path / tmux_bin axes."""
    from libtmux_mcp._exec import _tmux_argv

    assert _tmux_argv(t.cast("t.Any", server), *args) == expected


def test_every_libtmux_tmux_cmd_call_site_is_bounded() -> None:
    """A new libtmux call site must fail loudly, not silently unbind.

    The bound is installed by rebinding ``tmux_cmd`` in each libtmux
    module that constructs one. That is invisible to the type checker
    and to import-time errors, so an upgrade adding a call site in a
    fourth module would quietly restore the unbounded path that let
    ``break_pane`` hang for 150s. AST rather than text search: the
    ``tmux_cmd(...)`` lines in ``options.py`` are doctest examples
    inside docstrings, and only a parser can tell those from calls.

    This covers list COMPLETENESS -- it fires on a libtmux upgrade. That
    the list actually drives the binding is covered by the half-wedge
    regression test, which walks ``window.panes`` and so goes through
    ``neo``; that one fires on a refactor here.
    """
    import ast
    import importlib
    import pathlib

    import libtmux

    from libtmux_mcp._exec import _PATCHED_LIBTMUX_MODULES, _BoundedTmuxCmd

    root = pathlib.Path(libtmux.__file__).parent
    callers: set[str] = set()
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - defensive
            continue
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "tmux_cmd"
            for node in ast.walk(tree)
        ):
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            callers.add(".".join(["libtmux", *parts]))

    assert callers, "found no tmux_cmd call sites; the AST walk is broken"
    assert callers == set(_PATCHED_LIBTMUX_MODULES), (
        f"libtmux constructs tmux_cmd in {sorted(callers)} but only "
        f"{sorted(_PATCHED_LIBTMUX_MODULES)} are bounded"
    )
    for name in callers:
        module = importlib.import_module(name)
        assert module.tmux_cmd is _BoundedTmuxCmd, f"{name} is unbounded"


def test_bounded_tmux_cmd_matches_stock_output(mcp_server: Server) -> None:
    """The bounded replacement must answer exactly as stock does.

    Covers the three shapes that differ: a normal command, a command
    that fails on stderr, and ``has-session``, which libtmux reports
    through *stdout* rather than stderr.
    """
    from libtmux_mcp._exec import _BoundedTmuxCmd

    stock = _BoundedTmuxCmd.__bases__[0]
    socket_flag = f"-L{mcp_server.socket_name}"
    cases = (
        (socket_flag, "list-sessions", "-F", "#{session_id}"),
        (socket_flag, "list-panes", "-t", "%999999"),
        (socket_flag, "has-session", "-t", "definitely-absent"),
    )
    for args in cases:
        mine = _BoundedTmuxCmd(*args)
        theirs = stock(*args)
        assert (mine.returncode, mine.stdout, mine.stderr) == (
            theirs.returncode,
            theirs.stdout,
            theirs.stderr,
        ), f"diverged on {args[1]}"
