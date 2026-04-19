# Kill window

```{fastmcp-tool} window_tools.kill_window
```

**Use when** you're done with a window and all its panes.

**Avoid when** you only want to remove one pane — use {tooliconl}`kill-pane`.

**Side effects:** Destroys the window and all its panes. Not reversible.

**Example:**

```json
{
  "tool": "kill_window",
  "arguments": {
    "window_id": "@1"
  }
}
```

Response (string):

```text
Window killed: @1
```

```{fastmcp-tool-input} window_tools.kill_window
```
