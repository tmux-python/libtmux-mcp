```{fastmcp-tool} window_tools.list_panes
```

**Use when** you need to discover which panes exist in a window before
sending keys or capturing output.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "list_panes",
  "arguments": {
    "session_name": "dev"
  }
}
```

Response:

```json
[
  {
    "pane_id": "%0",
    "pane_index": "0",
    "pane_width": "80",
    "pane_height": "15",
    "pane_current_command": "zsh",
    "pane_current_path": "/home/user/myproject",
    "pane_pid": "12345",
    "pane_title": "build",
    "pane_active": "1",
    "window_id": "@0",
    "session_id": "$0",
    "is_caller": null
  },
  {
    "pane_id": "%1",
    "pane_index": "1",
    "pane_width": "80",
    "pane_height": "8",
    "pane_current_command": "zsh",
    "pane_current_path": "/home/user/myproject",
    "pane_pid": "12400",
    "pane_title": "",
    "pane_active": "0",
    "window_id": "@0",
    "session_id": "$0",
    "is_caller": null
  }
]
```

```{fastmcp-tool-input} window_tools.list_panes
```
