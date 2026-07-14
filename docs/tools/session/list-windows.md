# List windows

```{fastmcp-tool} session_tools.list_windows
```

**Use when** you need window names, indices, or layout metadata within a
session before selecting a window to work with.

**Avoid when** you need pane-level detail — use {tooliconl}`list-panes`.

**Side effects:** None. Readonly.

{tooliconl}`list-windows` keeps `scope="server"` as its default. With no
session selector, that lists windows across the effective server; existing
selectors still narrow the result. Set `scope="caller_session"` to list
windows in the session containing the frozen caller pane.
That scope performs an extra targeted tmux lookup; the added work buys
fail-closed live-session accuracy instead of trusting stale session metadata.

Caller-session scope cannot be combined with `session_name` or `session_id`.
It raises a tool error instead of widening the search when the MCP invocation
started outside tmux, the caller pane no longer resolves, or the caller socket
differs from the effective target.

Results arrive as a {class}`~libtmux_mcp.models.WindowPage`. The default
`detail="summary"` rows keep window identity, parent session, active state,
name, index, and pane count. Set `detail="full"` when you need layout or
dimensions; that returns more metadata. Filters run against full window
metadata before rows are projected, sorted, and paged. The default page uses
`limit=100` and `offset=0`.

**Example:**

```json
{
  "tool": "list_windows",
  "arguments": {
    "session_name": "dev"
  }
}
```

Response:

```json
{
  "items": [
    {
      "window_id": "@0",
      "window_name": "editor",
      "window_index": "1",
      "session_id": "$0",
      "session_name": "dev",
      "pane_count": 2,
      "window_active": "1",
      "active_pane_id": "%0"
    },
    {
      "window_id": "@1",
      "window_name": "server",
      "window_index": "2",
      "session_id": "$0",
      "session_name": "dev",
      "pane_count": 1,
      "window_active": "0",
      "active_pane_id": "%2"
    }
  ],
  "total": 2,
  "offset": 0,
  "limit": 100,
  "truncated": false
}
```

```{fastmcp-tool-input} session_tools.list_windows
```
