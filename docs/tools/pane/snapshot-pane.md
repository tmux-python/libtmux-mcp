# Snapshot pane

```{fastmcp-tool} pane_tools.snapshot_pane
```

**Use when** you need a complete picture of a pane in a single call — visible
text plus cursor position, whether the pane is in copy mode, scroll offset,
and scrollback history size. Replaces separate {tooliconl}`capture-pane` +
{tooliconl}`get-pane-info` calls when you need to reason about cursor location or
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
  "pane_left": 0,
  "pane_top": 0,
  "pane_right": 79,
  "pane_bottom": 23,
  "pane_at_left": true,
  "pane_at_right": true,
  "pane_at_top": true,
  "pane_at_bottom": true,
  "pane_tty": "/dev/pts/5",
  "pane_pid": "12345",
  "pane_dead": false,
  "alternate_on": false,
  "pane_in_mode": false,
  "pane_mode": null,
  "scroll_position": null,
  "history_size": 142,
  "title": null,
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "is_caller": false
}
```

The geometry block (`pane_left` / `pane_top` / `pane_right` /
`pane_bottom` and the four `pane_at_*` predicates) is fetched in the
same `display-message` round-trip as the cursor and mode fields, so
there is no extra tmux call. To target a layout-relative pane (e.g.
"the bottom-right pane") use {tooliconl}`find-pane-by-position`
instead of computing edges from this snapshot.

```{fastmcp-tool-input} pane_tools.snapshot_pane
```
