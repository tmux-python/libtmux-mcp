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
