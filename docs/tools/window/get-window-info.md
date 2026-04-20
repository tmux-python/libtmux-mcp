# Get window info

```{fastmcp-tool} window_tools.get_window_info
```

**Use when** you need metadata for a single window (name, index, layout,
dimensions, pane count) and you already know the `window_id` or
`window_index`. Avoids the `list_windows` + filter dance.

**Avoid when** you need every window in a session — call `list_panes` with
`session_id` or iterate through the session's windows via the
`tmux://sessions/{name}/windows` resource.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "get_window_info",
  "arguments": {
    "window_id": "@1"
  }
}
```

Response:

```json
{
  "window_id": "@1",
  "window_name": "editor",
  "window_index": "1",
  "session_id": "$0",
  "session_name": "dev",
  "pane_count": 2,
  "window_layout": "7f9f,80x24,0,0[80x15,0,0,0,80x8,0,16,1]",
  "window_active": "1",
  "window_width": "80",
  "window_height": "24"
}
```

Resolve by `window_index` when only the index is known — requires
`session_name` or `session_id` to disambiguate:

```json
{
  "tool": "get_window_info",
  "arguments": {
    "window_index": "1",
    "session_name": "dev"
  }
}
```

```{fastmcp-tool-input} window_tools.get_window_info
```
