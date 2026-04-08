```{fastmcp-tool} session_tools.create_window
```

**Use when** you need a new terminal workspace within an existing session.

**Side effects:** Creates a new window. Attaches to it if `attach` is true.

**Example:**

```json
{
  "tool": "create_window",
  "arguments": {
    "session_name": "dev",
    "window_name": "logs"
  }
}
```

Response:

```json
{
  "window_id": "@2",
  "window_name": "logs",
  "window_index": "3",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 1,
  "window_layout": "b25f,80x24,0,0,5",
  "window_active": "1",
  "window_width": "80",
  "window_height": "24"
}
```

```{fastmcp-tool-input} session_tools.create_window
```
