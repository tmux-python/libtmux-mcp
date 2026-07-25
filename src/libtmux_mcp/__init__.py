"""libtmux MCP server - programmatic tmux control for AI agents."""

from __future__ import annotations

import argparse
import sys
import typing as t

from .__about__ import __version__

__all__ = ["__version__"]


def _build_parser() -> argparse.ArgumentParser:
    """Build the local command-line parser."""
    parser = argparse.ArgumentParser(
        prog="libtmux-mcp",
        description="Run the libtmux MCP server over stdio.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"libtmux-mcp {__version__}",
    )
    return parser


def main(argv: t.Sequence[str] | None = None) -> None:
    """Entry point for the libtmux MCP server."""
    _build_parser().parse_args(argv)

    try:
        from libtmux_mcp.server import run_server
    except ImportError as exc:
        # Name the module that actually failed. This catch spans the
        # WHOLE server import tree, and it used to blame fastmcp for
        # everything it caught — which is close to the one cause it
        # cannot have, since fastmcp is a hard dependency. Measured
        # against mcp 2.0.0b2, which deleted ``mcp.types``: the server
        # printed "requires fastmcp" while fastmcp was installed and
        # importable.
        #
        # This string is the whole diagnosis. An MCP client sees one
        # stderr line before the pipe closes and then reports nothing
        # more useful than "Connection closed".
        blamed = (exc.name or "").split(".")[0]
        subject = f"cannot import {blamed!r}: {exc}" if blamed else str(exc)
        print(
            f"libtmux-mcp failed to start: {subject}. The installed "
            "dependency set is incomplete or incompatible — repair it "
            "with: pip install --force-reinstall libtmux-mcp",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    run_server()
