```{fastmcp-tool} session_tools.select_window
```

**Use when** you need to switch focus to a different window — by ID, index,
or direction (`next`, `previous`, `last`).

**Side effects:** Changes the active window in the session.

**Example:**

```json
{
  "tool": "select_window",
  "arguments": {
    "direction": "next",
    "session_name": "dev"
  }
}
```

Response:

```json
{
  "window_id": "@1",
  "window_name": "server",
  "window_index": "2",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 1,
  "window_layout": "b25f,80x24,0,0,2",
  "window_active": "1",
  "window_width": "80",
  "window_height": "24"
}
```

```{fastmcp-tool-input} session_tools.select_window
```
