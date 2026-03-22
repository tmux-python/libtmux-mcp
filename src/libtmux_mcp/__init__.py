"""libtmux MCP server - programmatic tmux control for AI agents."""

from __future__ import annotations


def main() -> None:
    """Entry point for the libtmux MCP server."""
    try:
        from libtmux_mcp.server import run_server
    except ImportError:
        import sys

        print(
            "libtmux-mcp requires fastmcp. Install with: pip install libtmux-mcp",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    run_server()
