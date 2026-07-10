# Split window

```{fastmcp-tool} window_tools.split_window
```

**Use when** you need side-by-side or stacked terminals within the same
window.

**Side effects:** Creates a new pane by splitting an existing one.

For MCP calls, an omitted `suppress_history` follows the startup default in {ref}`configuration`, and an explicit `true` or `false` wins. Direct Python calls default to `False`. When suppression is effective, {tooliconl}`split-window` adds an environment that applies to only the spawned process; it does not change the tmux session environment, and later panes do not inherit it. An explicit `false` prevents new controls but does not remove controls inherited from the session environment. Shell startup files can override the controls; see {ref}`history-hygiene` and {ref}`safety`.

The history policy only copies and merges environment values; it does not rewrite command text. The `shell` text is passed through unchanged. If you also pass `environment`, any history-control values must agree with the suppression policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression.

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
  "is_caller": false
}
```

```{fastmcp-tool-input} window_tools.split_window
```
