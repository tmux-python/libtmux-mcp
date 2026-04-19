# Swap pane

```{fastmcp-tool} pane_tools.swap_pane
```

**Use when** you want to rearrange pane positions without changing content —
e.g. moving a log pane from bottom to top.

**Side effects:** Exchanges the visual positions of two panes.

**Example:**

```json
{
  "tool": "swap_pane",
  "arguments": {
    "source_pane_id": "%0",
    "target_pane_id": "%1"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "1",
  "pane_width": "80",
  "pane_height": "11",
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

```{fastmcp-tool-input} pane_tools.swap_pane
```
