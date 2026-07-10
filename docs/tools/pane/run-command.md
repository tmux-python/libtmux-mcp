# Run command

```{fastmcp-tool} pane_tools.run_command
```

**Use when** you need to run a shell command in a pane and get a typed
result with exit status, timeout state, and captured pane output.

**Avoid when** you need raw interactive key driving — use
{tooliconl}`send-keys` or {tooliconl}`send-keys-batch` for TUIs, key
names, and partial commands.

**Side effects:** Sends a command to the pane's interactive shell. The command may read or write files, start processes, or access the network depending on what the shell command does. Each command runs in a subshell, so directory or environment changes do not persist across calls.

For MCP calls, the {ref}`configuration <configuration>` setting {envvar}`LIBTMUX_SUPPRESS_HISTORY` supplies the value when this argument is omitted, and an explicit `suppress_history` value wins. Direct Python calls default to `False`. Suppression is best effort: {tooliconl}`run-command` prefixes one space to the grouped event that carries the caller's single-line command, but the existing shell must be configured to ignore space-prefixed commands. When suppression is effective, a command containing a carriage return or line feed is rejected before tmux receives input because a prefix cannot protect each shell event. Set `suppress_history=false` to keep intentional multiline behavior. An explicit `false` does not change or undo that shell's history environment or startup configuration. Do not use history suppression as secret transport. See {ref}`history-hygiene` for shell behavior and {ref}`safety` before handling credentials.

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
