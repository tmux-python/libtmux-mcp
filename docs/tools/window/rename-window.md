# Rename window

```{fastmcp-tool} window_tools.rename_window
```

**Use when** a window name no longer reflects its purpose.

**Side effects:** Renames the window.

**Example:**

```json
{
  "tool": "rename_window",
  "arguments": {
    "session_name": "dev",
    "new_name": "build"
  }
}
```

Response:

```json
{
  "window_id": "@0",
  "window_name": "build",
  "window_index": "1",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 2,
  "window_layout": "7f9f,80x24,0,0[80x15,0,0,0,80x8,0,16,1]",
  "window_active": "1",
  "window_width": "80",
  "window_height": "24"
}
```

```{fastmcp-tool-input} window_tools.rename_window
```
