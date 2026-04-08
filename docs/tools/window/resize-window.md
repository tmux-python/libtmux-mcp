```{fastmcp-tool} window_tools.resize_window
```

**Use when** you need to adjust the window dimensions.

**Side effects:** Changes window size.

**Example:**

```json
{
  "tool": "resize_window",
  "arguments": {
    "session_name": "dev",
    "width": 120,
    "height": 40
  }
}
```

Response:

```json
{
  "window_id": "@0",
  "window_name": "editor",
  "window_index": "1",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 2,
  "window_layout": "baaa,120x40,0,0[120x20,0,0,0,120x19,0,21,1]",
  "window_active": "1",
  "window_width": "120",
  "window_height": "40"
}
```

```{fastmcp-tool-input} window_tools.resize_window
```
