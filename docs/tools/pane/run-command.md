# Run command

```{fastmcp-tool} pane_tools.run_command
```

**Use when** you need to run a shell command in a pane and get a typed
result with exit status, timeout state, and captured pane output.

**Avoid when** you need raw interactive key driving — use
{tooliconl}`send-keys` for TUIs, key names, and partial commands.

**Side effects:** Sends a command to the pane's interactive shell. The
command may read or write files, start processes, or access the network
depending on what the shell command does.

**Example:**

```json
{
  "tool": "run_command",
  "arguments": {
    "command": "pytest -q",
    "pane_id": "%2",
    "timeout": 60
  }
}
```

Response:

```json
{
  "pane_id": "%2",
  "exit_status": 0,
  "timed_out": false,
  "elapsed_seconds": 4.2,
  "output": ["..."],
  "output_truncated": false,
  "output_truncated_lines": 0
}
```

```{fastmcp-tool-input} pane_tools.run_command
```
