# Create window

```{fastmcp-tool} session_tools.create_window
```

**Use when** you need a new terminal workspace within an existing session.

**Side effects:** Creates a new window. Attaches to it if `attach` is true.

`suppress_persistent_history` defaults to `false` for MCP and direct Python calls. It does not inherit {envvar}`LIBTMUX_SUPPRESS_HISTORY`. Leave it `false` to add no history controls for this call. That choice cannot remove inherited, session, or startup-file controls.

Set it to `true` and {tooliconl}`create-window` copies and merges best-effort no-disk history controls for only the spawned process. It does not change the tmux session environment, so future windows and panes do not receive the controls from this call. The shell can retain in-memory history, and a startup file can override these controls after the process starts.

The history policy does not rewrite command text or tmux launch arguments. If you also pass `environment`, any history-control values must agree with the policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression. See {ref}`history-hygiene` for shell behavior and {ref}`safety` for output, scrollback, process, transcript, hook, and logging boundaries.

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
