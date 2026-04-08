```{fastmcp-tool} pane_tools.resize_pane
```

**Use when** you need to adjust pane dimensions.

**Side effects:** Changes pane size. May affect adjacent panes.

**Example:**

```json
{
  "tool": "resize_pane",
  "arguments": {
    "pane_id": "%0",
    "height": 15
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "0",
  "pane_width": "80",
  "pane_height": "15",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": null
}
```

```{fastmcp-tool-input} pane_tools.resize_pane
```
