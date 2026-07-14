(pagination-overview)=

# Pagination

The
[MCP pagination spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination)
defines opaque cursors for list-style protocol calls.
[FastMCP](https://gofastmcp.com) supports that protocol pagination
when a server is configured with
``list_page_size``. libtmux-mcp does not currently configure
protocol-level list pagination, so its registry lists normally return
as one page under FastMCP's defaults.

## Where cursors and pages show up

### Protocol-level list calls

``tools/list`` / ``prompts/list`` / ``resources/list`` /
``resources/templates/list`` are registry-list calls. In this server's
current configuration, clients should expect those lists to arrive in
one response unless libtmux-mcp later enables FastMCP's
``list_page_size`` setting.

### Tool result paging

Hierarchy discovery tools own a typed paging surface because tmux can hold
more rows than one useful discovery response:

- {tool}`list-servers` returns a
  {class}`~libtmux_mcp.models.ServerPage`.
- {tool}`list-sessions` returns a
  {class}`~libtmux_mcp.models.SessionPage`.
- {tool}`list-windows` returns a
  {class}`~libtmux_mcp.models.WindowPage`.
- {tool}`list-panes` returns a {class}`~libtmux_mcp.models.PanePage`.

Each page has ``items``, ``total``, ``offset``, ``limit``, and ``truncated``.
The default is `limit=100` and `offset=0`. Filters run before the stable sort,
projection, and page slice, so ``total`` always describes every matching row.
When ``truncated`` is true, add the number of returned items to ``offset`` and
call again. Window and pane lists default to compact summary rows; pass
`detail="full"` only when you need the larger metadata projection.

{tool}`search-panes` has a separate
{class}`~libtmux_mcp.models.SearchPanesResult` wrapper with ``matches``,
``truncated``, ``truncated_panes``, ``total_panes_matched``, ``offset``, and
``limit`` because it also reports per-pane content truncation.

These are application-level pages (not MCP-cursor pagination) — the agent
decides how many rows it needs and when to stop.

### Observation cursors

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
end-to-end**: the tool / prompt / resource registries.

Tool-level paging and observation cursors are for **state derived from live
tmux**. Hierarchy lists need deterministic, bounded discovery rows. Capturing
every pane's contents and running a regex has a different cost, and repeated
reads of one pane need an observation checkpoint rather than a collection
offset. libtmux-mcp exposes each contract separately instead of pretending
live terminal state is one stable list.

## Further reading

- [MCP pagination spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination)
- {class}`~libtmux_mcp.models.ListPage` — shared hierarchy-page fields
- {class}`~libtmux_mcp.models.SearchPanesResult` — the structured
  wrapper for {toolref}`search-panes`
- {tool}`search-panes` — the tool itself
- {class}`~libtmux_mcp.models.CaptureSinceResult` — the structured
  response for {toolref}`capture-since`
- {tool}`capture-since` — incremental observation for a known pane
