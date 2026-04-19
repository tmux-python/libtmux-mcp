# Split window

```{fastmcp-tool} window_tools.split_window
```

**Use when** you need side-by-side or stacked terminals within the same
window.

**Side effects:** Creates a new pane by splitting an existing one.

**Example:**

```json
{
  "tool": "split_window",
  "arguments": {
    "session_name": "dev",
    "direction": "right"
  }
}
```

Response:

```json
{
  "pane_id": "%4",
  "pane_index": "1",
  "pane_width": "39",
  "pane_height": "24",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "3732",
  "pane_title": "",
  "pane_active": "0",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} window_tools.split_window
```
