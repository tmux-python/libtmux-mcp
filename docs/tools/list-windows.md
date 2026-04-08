```{fastmcp-tool} session_tools.list_windows
```

**Use when** you need window names, indices, or layout metadata within a
session before selecting a window to work with.

**Avoid when** you need pane-level detail — use {tooliconl}`list-panes`.

**Side effects:** None. Readonly.

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
[
  {
    "window_id": "@0",
    "window_name": "editor",
    "window_index": "1",
    "session_id": "$0",
    "session_name": "dev",
    "pane_count": 2,
    "window_layout": "c195,80x24,0,0[80x12,0,0,0,80x11,0,13,1]",
    "window_active": "1",
    "window_width": "80",
    "window_height": "24"
  },
  {
    "window_id": "@1",
    "window_name": "server",
    "window_index": "2",
    "session_id": "$0",
    "session_name": "dev",
    "pane_count": 1,
    "window_layout": "b25f,80x24,0,0,2",
    "window_active": "0",
    "window_width": "80",
    "window_height": "24"
  }
]
```

```{fastmcp-tool-input} session_tools.list_windows
```
