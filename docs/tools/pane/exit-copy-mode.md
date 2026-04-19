# Exit copy mode

```{fastmcp-tool} pane_tools.exit_copy_mode
```

**Use when** you're done scrolling through scrollback and want the pane to
resume receiving output.

**Side effects:** Exits copy mode, returning the pane to normal.

**Example:**

```json
{
  "tool": "exit_copy_mode",
  "arguments": {
    "pane_id": "%0"
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
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.exit_copy_mode
```
