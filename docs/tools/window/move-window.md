# Move window

```{fastmcp-tool} window_tools.move_window
```

**Use when** you need to reorder windows within a session or move a window
to a different session entirely.

**Side effects:** Changes the window's index or parent session.

**Example:**

```json
{
  "tool": "move_window",
  "arguments": {
    "window_id": "@1",
    "destination_index": "1"
  }
}
```

Response:

```json
{
  "window_id": "@1",
  "window_name": "server",
  "window_index": "1",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 1,
  "window_layout": "b25f,80x24,0,0,2",
  "window_active": "0",
  "window_width": "80",
  "window_height": "24"
}
```

```{fastmcp-tool-input} window_tools.move_window
```

## Destroy
