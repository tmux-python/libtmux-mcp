# Get pane info

```{fastmcp-tool} pane_tools.get_pane_info
```

**Use when** you need pane dimensions, PID, current working directory, or
other metadata without reading the terminal content.

**Avoid when** you need the actual text — use {tooliconl}`capture-pane`.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "get_pane_info",
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
  "pane_left": 0,
  "pane_top": 0,
  "pane_right": 79,
  "pane_bottom": 23,
  "pane_at_left": true,
  "pane_at_right": true,
  "pane_at_top": true,
  "pane_at_bottom": true,
  "pane_tty": "/dev/pts/5",
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

Coordinates are window-relative cell offsets. `pane_left` / `pane_top` are
0-based and `pane_right` / `pane_bottom` are inclusive. The four
`pane_at_*` predicates account for `pane-border-status`. To target a
layout-relative pane (e.g. "the bottom-right pane") use
{tooliconl}`find-pane-by-position` instead of computing edges yourself.

```{fastmcp-tool-input} pane_tools.get_pane_info
```
