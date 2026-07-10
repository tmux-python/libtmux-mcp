# Create window

```{fastmcp-tool} session_tools.create_window
```

**Use when** you need a new terminal workspace within an existing session.

**Side effects:** Creates a new window. Attaches to it if `attach` is true.

For MCP calls, an omitted `suppress_history` follows the startup default in {ref}`configuration`, and an explicit `true` or `false` wins. Direct Python calls default to `False`. When suppression is effective, {tooliconl}`create-window` adds an environment that applies to only the spawned process; it does not change the tmux session environment, and future windows or panes do not inherit it. An explicit `false` prevents new controls but does not remove controls inherited from the session environment. Shell startup files can override the controls; see {ref}`history-hygiene` and {ref}`safety`.

The history policy only copies and merges environment values; it does not rewrite command text or tmux launch arguments. If you also pass `environment`, any history-control values must agree with the suppression policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression.

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
  "window_height": "24",
  "active_pane_id": "%5"
}
```

```{fastmcp-tool-input} session_tools.create_window
```
