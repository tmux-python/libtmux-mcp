"""Tests for the libtmux-mcp console entry point."""

from __future__ import annotations

import typing as t

import pytest

from libtmux_mcp import __version__, main


class CliFlagFixture(t.NamedTuple):
    """Test fixture for local CLI options."""

    test_id: str
    argv: list[str]
    expected_stdout: str


CLI_FLAG_FIXTURES: list[CliFlagFixture] = [
    CliFlagFixture("help", ["--help"], "usage:"),
    CliFlagFixture("version", ["--version"], __version__),
]


@pytest.mark.parametrize(
    CliFlagFixture._fields,
    CLI_FLAG_FIXTURES,
    ids=[f.test_id for f in CLI_FLAG_FIXTURES],
)
def test_main_local_flags_exit_without_starting_server(
    test_id: str,
    argv: list[str],
    expected_stdout: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Local CLI flags exit before starting the MCP server."""
    assert test_id

    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code == 0
    assert expected_stdout in capsys.readouterr().out


class ImportFailureFixture(t.NamedTuple):
    """Test fixture for the entry point's dependency diagnostics."""

    test_id: str
    #: Value of ``ImportError.name`` — the module the import machinery
    #: blames, which is the top-level package even when a SUBmodule is
    #: what went missing.
    missing_name: str | None
    message: str
    expected_fragments: list[str]


IMPORT_FAILURE_FIXTURES: list[ImportFailureFixture] = [
    # Regression: a bare `except ImportError` spans the whole server
    # import tree, so this case used to print "requires fastmcp" while
    # fastmcp was installed and importable. Measured against mcp 2.0.0b2,
    # which deleted `mcp.types`; over MCP the client saw only
    # "Connection closed" plus that one misleading line.
    ImportFailureFixture(
        test_id="other_dependency_missing",
        missing_name="mcp",
        message="No module named 'mcp.types'",
        expected_fragments=["cannot import 'mcp'", "mcp.types"],
    ),
    ImportFailureFixture(
        test_id="fastmcp_missing",
        missing_name="fastmcp",
        message="No module named 'fastmcp'",
        expected_fragments=["cannot import 'fastmcp'", "force-reinstall"],
    ),
    ImportFailureFixture(
        test_id="unnamed_import_error",
        missing_name=None,
        message="cannot import name 'Whatever' from 'somewhere'",
        expected_fragments=["failed to start", "Whatever"],
    ),
]


@pytest.mark.parametrize(
    ImportFailureFixture._fields,
    IMPORT_FAILURE_FIXTURES,
    ids=[f.test_id for f in IMPORT_FAILURE_FIXTURES],
)
def test_main_names_the_dependency_that_actually_failed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    test_id: str,
    missing_name: str | None,
    message: str,
    expected_fragments: list[str],
) -> None:
    """A failed server import must name the module that failed.

    An MCP client sees exactly one stderr line before the pipe closes and
    then reports "Connection closed", so this string is the entire
    diagnosis available to whoever has to fix it. It used to blame
    fastmcp for everything — close to the one cause it cannot be, since
    fastmcp is a hard dependency and the package cannot install without
    it.
    """
    assert test_id
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: t.Any, **kwargs: t.Any) -> t.Any:
        if name == "libtmux_mcp.server":
            raise ImportError(message, name=missing_name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(__import__("sys").modules, "libtmux_mcp.server", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    for fragment in expected_fragments:
        assert fragment in err, f"{fragment!r} not in {err!r}"
