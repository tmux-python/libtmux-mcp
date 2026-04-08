```{fastmcp-tool} window_tools.select_layout
```

**Use when** you want to rearrange panes — `even-horizontal`,
`even-vertical`, `main-horizontal`, `main-vertical`, or `tiled`.

**Side effects:** Rearranges all panes in the window.

**Example:**

```json
{
  "tool": "select_layout",
  "arguments": {
    "session_name": "dev",
    "layout": "even-vertical"
  }
}
```

Response:

```json
{
  "window_id": "@0",
  "window_name": "editor",
  "window_index": "1",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 2,
  "window_layout": "even-vertical,80x24,0,0[80x12,0,0,0,80x11,0,13,1]",
  "window_active": "1",
  "window_width": "80",
  "window_height": "24"
}
```

```{fastmcp-tool-input} window_tools.select_layout
```
