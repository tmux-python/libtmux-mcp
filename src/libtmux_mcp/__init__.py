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
        # Name the module that actually failed: this catch spans the WHOLE
        # server import tree, so blaming fastmcp names the one cause it
        # cannot have -- it is a hard dependency. Under mcp 2.0.0b2, which
        # deleted ``mcp.types``, that reads "requires fastmcp" while
        # fastmcp is installed and importable.
        #
        # This string is the whole diagnosis: an MCP client sees one stderr
        # line before the pipe closes, then reports only "Connection
        # closed".
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
