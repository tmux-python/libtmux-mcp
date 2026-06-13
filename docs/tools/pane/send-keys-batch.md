# Send keys batch

```{fastmcp-tool} pane_tools.send_keys_batch
```

**Use when** you need to send several ordered raw-input operations to
one or more panes: TUI keystrokes, partial shell input, or persistent
shell interaction that should remain below the command-completion
layer.

**Avoid when** you need to run shell commands and capture results —
use {tooliconl}`run-command` for authored commands, or combine
{tooliconl}`send-keys` with {tooliconl}`capture-since` when later
observation is intentionally separate from input.

**Side effects:** Sends keystrokes to target panes in order. With
`on_error="stop"` the batch stops at the first failed operation and
returns that failure in the result. With `on_error="continue"` later
operations are still attempted.

**Example:**

```json
{
  "tool": "send_keys_batch",
  "arguments": {
    "operations": [
      {"pane_id": "%2", "keys": "C-c", "enter": false},
      {"pane_id": "%2", "keys": "npm run dev"}
    ],
    "on_error": "stop"
  }
}
```

Response:

```json
{
  "results": [
    {
      "index": 0,
      "pane_id": "%2",
      "success": true,
      "error": null,
      "elapsed_seconds": 0.01
    },
    {
      "index": 1,
      "pane_id": "%2",
      "success": true,
      "error": null,
      "elapsed_seconds": 0.01
    }
  ],
  "succeeded": 2,
  "failed": 0,
  "stopped_at": null
}
```

```{fastmcp-tool-input} pane_tools.send_keys_batch
```
