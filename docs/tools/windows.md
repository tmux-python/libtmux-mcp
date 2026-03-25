# Windows

## Inspect

```{fastmcp-tool} session_tools.list_windows
```

**Use when** you need window names, indices, or layout metadata within a
session before selecting a window to work with.

**Avoid when** you need pane-level detail — use {ref}`list-panes`.

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
    "direction": "horizontal"
  }
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

**Avoid when** you only want to remove one pane — use {ref}`kill-pane`.

**Side effects:** Destroys the window and all its panes. Not reversible.

```{fastmcp-tool-input} window_tools.kill_window
```
