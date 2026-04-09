```{fastmcp-tool} pane_tools.enter_copy_mode
```

**Use when** you need to scroll through scrollback history in a pane.
Optionally scroll up immediately after entering. Use
{tooliconl}`snapshot-pane` afterward to read the `scroll_position` and
visible content.

**Side effects:** Puts the pane into copy mode. The pane stops receiving
new output until you exit copy mode.

**Example:**

```json
{
  "tool": "enter_copy_mode",
  "arguments": {
    "pane_id": "%0",
    "scroll_up": 50
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
  "is_caller": null
}
```

```{fastmcp-tool-input} pane_tools.enter_copy_mode
```
