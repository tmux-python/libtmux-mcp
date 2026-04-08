```{fastmcp-tool} pane_tools.snapshot_pane
```

**Use when** you need a complete picture of a pane in a single call — visible
text plus cursor position, whether the pane is in copy mode, scroll offset,
and scrollback history size. Replaces separate `capture_pane` +
`get_pane_info` calls when you need to reason about cursor location or
terminal mode.

**Avoid when** you only need raw text — {tooliconl}`capture-pane` is lighter.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "snapshot_pane",
  "arguments": {
    "pane_id": "%0"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "content": "$ npm test\n\nPASS src/auth.test.ts\nTests: 3 passed\n$",
  "cursor_x": 2,
  "cursor_y": 4,
  "pane_width": 80,
  "pane_height": 24,
  "pane_in_mode": false,
  "pane_mode": null,
  "scroll_position": null,
  "history_size": 142,
  "title": "",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "is_caller": null
}
```

```{fastmcp-tool-input} pane_tools.snapshot_pane
```
