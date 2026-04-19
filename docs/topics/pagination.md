(pagination-overview)=

# Pagination

libtmux-mcp follows the
[MCP pagination spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination):
``tools/list``, ``prompts/list``, ``resources/list``, and
``resources/templates/list`` all return an opaque ``nextCursor`` when
a page is truncated, and accept ``cursor`` on the next call to
resume.

## Two places pagination shows up

### Protocol-level list calls

FastMCP handles ``tools/list`` / ``prompts/list`` / ``resources/list``
/ ``resources/templates/list`` pagination automatically. Neither
libtmux-mcp nor the agent needs to do anything: the server chooses
a sensible page size, encodes the cursor in an opaque base64 blob,
and replays state from it. Callers only need to thread through
``nextCursor`` if they consume the raw MCP protocol.

### Tool-level pagination on ``search_panes``

One libtmux-mcp tool owns its own pagination surface because a
single tmux server can carry tens of thousands of pane lines:

- {tool}`search-panes` returns a
  {class}`~libtmux_mcp.models.SearchPanesResult` wrapper with
  ``matches``, ``truncated``, ``truncated_panes``,
  ``total_panes_matched``, ``offset``, and ``limit``.
- Agents detect ``truncated=True`` and re-call with a higher
  ``offset`` to page through the match set.

This is application-level pagination (not MCP-cursor pagination) —
the agent decides how many matches it needs and when to stop.

## Why separate paths

Protocol-level cursors are for **collections the server owns
end-to-end**: the tool / prompt / resource registries. The server
knows what it has, so an opaque cursor is cheap.

Tool-level pagination is for **collections derived from live tmux
state**: capturing every pane's contents and running a regex is
expensive, and the result set can change mid-scan (new panes open,
old ones close). Exposing ``offset`` / ``limit`` lets the agent
bound cost explicitly, without pretending the snapshot is stable.

## Further reading

- [MCP pagination spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination)
- {class}`~libtmux_mcp.models.SearchPanesResult` — the structured
  wrapper for ``search_panes``
- {tool}`search-panes` — the tool itself
