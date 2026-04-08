```{fastmcp-tool} pane_tools.set_pane_title
```

**Use when** you want to label a pane for identification.

**Side effects:** Changes the pane title.

**Example:**

```json
{
  "tool": "set_pane_title",
  "arguments": {
    "pane_id": "%0",
    "title": "build"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "0",
  "pane_width": "80",
  "pane_height": "24",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "build",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": null
}
```

```{fastmcp-tool-input} pane_tools.set_pane_title
```
