# Find pane by position

```{fastmcp-tool} pane_tools.find_pane_by_position
```

**Use when** you need to act on a layout-relative pane — "the bottom-right
pane", "whichever pane is in the top-left" — without listing every pane and
computing geometry yourself.

**Avoid when** you already know the `pane_id`. Use {tooliconl}`get-pane-info`
or {tooliconl}`select-pane` directly.

**Side effects:** None. Read-only.

**Example:**

```json
{
  "tool": "find_pane_by_position",
  "arguments": {
    "corner": "bottom-right",
    "window_id": "@0"
  }
}
```

Response is a {class}`~libtmux_mcp.models.PaneInfo` for the pane occupying
that corner. The new geometry fields make the result self-describing:

```json
{
  "pane_id": "%3",
  "pane_left": 40,
  "pane_top": 12,
  "pane_right": 79,
  "pane_bottom": 23,
  "pane_at_left": false,
  "pane_at_right": true,
  "pane_at_top": false,
  "pane_at_bottom": true,
  "pane_tty": "/dev/pts/5"
}
```

**Tie-break.** When multiple panes satisfy both edge predicates (a
single-pane window touches every edge; some custom layouts can produce
ambiguous corners) the visually innermost pane wins — the one with the
largest `pane_left + pane_top`.

```{fastmcp-tool-input} pane_tools.find_pane_by_position
```
