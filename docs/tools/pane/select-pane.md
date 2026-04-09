```{fastmcp-tool} pane_tools.select_pane
```

**Use when** you need to focus a specific pane — by ID for a known target,
or by direction (`up`, `down`, `left`, `right`, `last`, `next`, `previous`)
to navigate a multi-pane layout.

**Side effects:** Changes the active pane in the window.

**Example:**

```json
{
  "tool": "select_pane",
  "arguments": {
    "direction": "down",
    "window_id": "@0"
  }
}
```

Response:

```json
{
  "pane_id": "%1",
  "pane_index": "1",
  "pane_width": "80",
  "pane_height": "11",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12400",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": null
}
```

```{fastmcp-tool-input} pane_tools.select_pane
```
