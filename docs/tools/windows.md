# Windows

## Inspect

```{fastmcp-tool} session_tools.list_windows
```

**Use when** you need window names, indices, or layout metadata within a
session before selecting a window to work with.

**Avoid when** you need pane-level detail — use {tool}`list-panes`.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "list_windows",
  "arguments": {
    "session_name": "dev"
  }
}
```

Response:

```json
[
  {
    "window_id": "@0",
    "window_name": "editor",
    "window_index": "1",
    "session_id": "$0",
    "session_name": "dev",
    "pane_count": 2,
    "window_layout": "c195,80x24,0,0[80x12,0,0,0,80x11,0,13,1]",
    "window_active": "1",
    "window_width": "80",
    "window_height": "24"
  },
  {
    "window_id": "@1",
    "window_name": "server",
    "window_index": "2",
    "session_id": "$0",
    "session_name": "dev",
    "pane_count": 1,
    "window_layout": "b25f,80x24,0,0,2",
    "window_active": "0",
    "window_width": "80",
    "window_height": "24"
  }
]
```

```{fastmcp-tool-input} session_tools.list_windows
```

---

```{fastmcp-tool} window_tools.list_panes
```

**Use when** you need to discover which panes exist in a window before
sending keys or capturing output.

**Side effects:** None. Readonly.

```{fastmcp-tool-input} window_tools.list_panes
```

## Act

```{fastmcp-tool} session_tools.create_window
```

**Use when** you need a new terminal workspace within an existing session.

**Side effects:** Creates a new window. Attaches to it if `attach` is true.

**Example:**

```json
{
  "tool": "create_window",
  "arguments": {
    "session_name": "dev",
    "window_name": "logs"
  }
}
```

Response:

```json
{
  "window_id": "@2",
  "window_name": "logs",
  "window_index": "3",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 1,
  "window_layout": "b25f,80x24,0,0,5",
  "window_active": "1",
  "window_width": "80",
  "window_height": "24"
}
```

```{fastmcp-tool-input} session_tools.create_window
```

---

```{fastmcp-tool} window_tools.split_window
```

**Use when** you need side-by-side or stacked terminals within the same
window.

**Side effects:** Creates a new pane by splitting an existing one.

**Example:**

```json
{
  "tool": "split_window",
  "arguments": {
    "session_name": "dev",
    "direction": "right"
  }
}
```

Response:

```json
{
  "pane_id": "%4",
  "pane_index": "1",
  "pane_width": "39",
  "pane_height": "24",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "3732",
  "pane_title": "",
  "pane_active": "0",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": null
}
```

```{fastmcp-tool-input} window_tools.split_window
```

---

```{fastmcp-tool} window_tools.rename_window
```

**Use when** a window name no longer reflects its purpose.

**Side effects:** Renames the window.

```{fastmcp-tool-input} window_tools.rename_window
```

---

```{fastmcp-tool} window_tools.select_layout
```

**Use when** you want to rearrange panes — `even-horizontal`,
`even-vertical`, `main-horizontal`, `main-vertical`, or `tiled`.

**Side effects:** Rearranges all panes in the window.

```{fastmcp-tool-input} window_tools.select_layout
```

---

```{fastmcp-tool} window_tools.resize_window
```

**Use when** you need to adjust the window dimensions.

**Side effects:** Changes window size.

```{fastmcp-tool-input} window_tools.resize_window
```

## Destroy

```{fastmcp-tool} window_tools.kill_window
```

**Use when** you're done with a window and all its panes.

**Avoid when** you only want to remove one pane — use {tool}`kill-pane`.

**Side effects:** Destroys the window and all its panes. Not reversible.

```{fastmcp-tool-input} window_tools.kill_window
```
