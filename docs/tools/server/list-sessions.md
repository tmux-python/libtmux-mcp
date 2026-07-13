# List sessions

```{fastmcp-tool} server_tools.list_sessions
```

**Use when** you need session names, IDs, or attached status before deciding
which session to target.

**Avoid when** you need window or pane details — use {tooliconl}`list-windows` or
{tooliconl}`list-panes` instead.

**Side effects:** None. Readonly.

Results arrive as a {class}`~libtmux_mcp.models.SessionPage`. Read session rows
from `items`; `total` reports every matching row before paging. The default
page uses `limit=100` and `offset=0`. When `truncated` is true, increase
`offset` to continue from the next row.

**Example:**

```json
{
  "tool": "list_sessions",
  "arguments": {}
}
```

Response:

```json
{
  "items": [
    {
      "session_id": "$0",
      "session_name": "myproject",
      "window_count": 2,
      "session_attached": "0",
      "session_created": "1774521871",
      "active_pane_id": "%0",
      "server_started": false
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 100,
  "truncated": false
}
```

```{fastmcp-tool-input} server_tools.list_sessions
```
