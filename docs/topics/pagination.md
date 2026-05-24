(pagination-overview)=

# Pagination

libtmux-mcp follows the
[MCP pagination spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination):
``tools/list``, ``prompts/list``, ``resources/list``, and
``resources/templates/list`` all return an opaque ``nextCursor`` when
a page is truncated, and accept ``cursor`` on the next call to
resume.

## Where cursors and pages show up

### Protocol-level list calls

FastMCP handles ``tools/list`` / ``prompts/list`` / ``resources/list``
/ ``resources/templates/list`` pagination automatically. Neither
libtmux-mcp nor the agent needs to do anything: the server chooses
a sensible page size, encodes the cursor in an opaque base64 blob,
and replays state from it. Callers only need to thread through
``nextCursor`` if they consume the raw MCP protocol.

### Tool-level result paging on ``search_panes``

One libtmux-mcp tool owns its own paging surface because a
single tmux server can carry tens of thousands of pane lines:

- {tool}`search-panes` returns a
  {class}`~libtmux_mcp.models.SearchPanesResult` wrapper with
  ``matches``, ``truncated``, ``truncated_panes``,
  ``total_panes_matched``, ``offset``, and ``limit``.
- Agents detect ``truncated=True`` and re-call with a higher
  ``offset`` to page through the match set.

This is application-level paging (not MCP-cursor pagination) —
the agent decides how many matches it needs and when to stop.

### Tool-level observation cursors on ``capture_since``

{tool}`capture-since` also has a ``cursor`` parameter, but it is
not a pagination cursor. The first call captures the current visible
pane and returns an opaque observation checkpoint. Follow-up calls
pass that cursor back to receive only rows written or rewritten after
the checkpoint while tmux still retains the needed history.

Because the cursor points into live tmux grid state, it has different
failure modes from protocol pagination:

- If the pane output scrolls into retained history, the cursor can
  still produce an exact delta.
- If tmux clears or trims the needed history, the response sets
  ``lines_missed=True`` and returns a conservative current visible
  capture with a fresh cursor.
- If the pane dies or is respawned, the cursor is invalid because it
  would otherwise point at a different process's terminal state.

This is application-level observation, not a stable collection scan.
Use it to reduce repeated pane reads, not to page through search
matches.

## Why separate paths

Protocol-level cursors are for **collections the server owns
end-to-end**: the tool / prompt / resource registries. The server
knows what it has, so an opaque cursor is cheap.

Tool-level paging and observation cursors are for **state derived
from live tmux panes**. Capturing every pane's contents and running a
regex is expensive, and the result set can change mid-scan (new panes
open, old ones close). Repeatedly reading one pane has the opposite
cost shape: the target is known, but unchanged scrollback wastes
model context. libtmux-mcp exposes each contract separately instead
of pretending live terminal state is one stable list.

## Further reading

- [MCP pagination spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination)
- {class}`~libtmux_mcp.models.SearchPanesResult` — the structured
  wrapper for ``search_panes``
- {tool}`search-panes` — the tool itself
- {class}`~libtmux_mcp.models.CaptureSinceResult` — the structured
  response for ``capture_since``
- {tool}`capture-since` — incremental observation for a known pane
