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
    except ImportError:
        print(
            "libtmux-mcp requires fastmcp. Install with: pip install libtmux-mcp",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    run_server()
