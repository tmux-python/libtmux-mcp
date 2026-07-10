# Split window

```{fastmcp-tool} window_tools.split_window
```

**Use when** you need side-by-side or stacked terminals within the same
window.

**Side effects:** Creates a new pane by splitting an existing one.

`suppress_persistent_history` defaults to `false` for MCP and direct Python calls. It does not inherit {envvar}`LIBTMUX_SUPPRESS_HISTORY`. Leave it `false` to add no history controls for this call. That choice cannot remove inherited, session, or startup-file controls.

Set it to `true` and {tooliconl}`split-window` copies and merges best-effort no-disk history controls for only the spawned process. It does not change the tmux session environment, so later panes do not receive the controls from this call. The shell can retain in-memory history, and a startup file can override these controls after the process starts.

The history policy does not rewrite command text. The `shell` text is passed through unchanged. If you also pass `environment`, any history-control values must agree with the policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression. See {ref}`history-hygiene` for shell behavior and {ref}`safety` for output, scrollback, process, transcript, hook, and logging boundaries.

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
