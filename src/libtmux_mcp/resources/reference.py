"""Static reference resources for tmux primitives.

Why a resource and not a tool: tmux format strings (``#{pane_id}``,
``#{pane_in_mode}``, ``#{?cond,then,else}``) are a closed reference
catalog, not a query. Agents that hit a ``#{...}`` field they don't
recognize need a fixed lookup, not a tool round-trip. Exposing this as
an MCP resource lets clients pull it on demand and lets the agent
recover from an unknown-format-name guess without paying a
``display_message`` round-trip just to discover what's available.
"""

from __future__ import annotations

import textwrap
import typing as t

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


#: MIME type for the format-string reference resource. Markdown gives
#: clients enough structure to render headings and code spans without
#: requiring a richer content type.
_MARKDOWN_MIME = "text/markdown"


_FORMAT_STRING_REFERENCE = textwrap.dedent("""\
    # tmux format strings

    Pass via ``display_message(format_string="#{...}")`` or any other
    tool that accepts a tmux format expression.

    ## Pane

    - ``#{pane_id}`` — globally unique pane identifier (e.g. ``%1``)
    - ``#{pane_index}`` — index within the window
    - ``#{pane_current_command}`` — foreground command name
    - ``#{pane_current_path}`` — current working directory
    - ``#{pane_pid}`` — pane process PID
    - ``#{pane_dead}`` — ``1`` when the pane's process has exited
    - ``#{pane_in_mode}`` — ``1`` when the pane is in copy/scroll mode
    - ``#{pane_mode}`` — current pane mode name when in mode
    - ``#{pane_active}`` — ``1`` for the active pane in its window
    - ``#{pane_width}`` / ``#{pane_height}`` — pane dimensions in cells
    - ``#{cursor_x}`` / ``#{cursor_y}`` — cursor position within the pane
    - ``#{scroll_position}`` — scrollback position when in copy mode

    ## Window

    - ``#{window_id}`` — globally unique window identifier (e.g. ``@1``)
    - ``#{window_index}`` — window index within the session
    - ``#{window_name}`` — window name
    - ``#{window_zoomed_flag}`` — ``1`` when a pane is zoomed
    - ``#{window_layout}`` — current layout string
    - ``#{window_panes}`` — number of panes in the window
    - ``#{window_active}`` — ``1`` for the active window in its session

    ## Session

    - ``#{session_id}`` — globally unique session identifier (e.g. ``$1``)
    - ``#{session_name}`` — session name
    - ``#{session_attached}`` — ``1`` when at least one client is attached
    - ``#{session_windows}`` — number of windows in the session

    ## Server / client

    - ``#{host}`` — hostname running the tmux server
    - ``#{client_tty}`` — TTY of the client (when evaluated client-side)
    - ``#{socket_path}`` — server socket path

    ## Conditionals and string operations

    - ``#{?cond,then,else}`` — emit ``then`` if ``cond`` is truthy, else
      ``else``
    - ``#{C/i:pattern}`` — case-insensitive search inside the result
    - ``#{=N:expr}`` — truncate ``expr`` to ``N`` characters
    - ``#{s/from/to/:expr}`` — substitution
    - ``#{T:expr}`` — recursively expand format strings within ``expr``

    See ``man tmux`` (FORMATS section) for the complete catalog.
""")


def register(mcp: FastMCP) -> None:
    """Register reference resources with the FastMCP instance."""

    @mcp.resource(
        "tmux://reference/format-strings",
        title="tmux Format String Reference",
        mime_type=_MARKDOWN_MIME,
    )
    def get_format_string_reference() -> str:
        """Return the tmux format-string cheat sheet as Markdown.

        Static reference content. Use this when an agent encounters
        an unfamiliar ``#{...}`` field — pulling the resource is
        cheaper than a ``display_message`` round-trip and avoids
        hallucinated format names.
        """
        return _FORMAT_STRING_REFERENCE

    # Type checkers: list the function to silence unused-name warnings
    # without exposing it outside this closure.
    _ = (get_format_string_reference,)


__all__ = ["register"]
