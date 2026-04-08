```{fastmcp-tool} server_tools.list_sessions
```

**Use when** you need session names, IDs, or attached status before deciding
which session to target.

**Avoid when** you need window or pane details — use {tooliconl}`list-windows` or
{tooliconl}`list-panes` instead.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "list_sessions",
  "arguments": {}
}
```

Response:

```json
[
  {
    "session_id": "$0",
    "session_name": "myproject",
    "window_count": 2,
    "session_attached": "0",
    "session_created": "1774521871"
  }
]
```

```{fastmcp-tool-input} server_tools.list_sessions
```
