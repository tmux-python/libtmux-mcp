# Call mutating tools batch

```{fastmcp-tool} batch_tools.call_mutating_tools_batch
```

**Use when** you need an ordered workflow made from existing typed MCP
tools, such as renaming and splitting a known window, while preserving
each tool's own schema and safety checks.

**Avoid when** you need tmux's native semicolon command parsing. This
tool batches MCP tools; it does not create one tmux command sequence.
For shell commands with completion and output, prefer
{tooliconl}`run-command`.

**Side effects:** Runs readonly and mutating nested tools in order.
Destructive nested tools are rejected even when the server process is
running with `LIBTMUX_SAFETY=destructive`.

**Example:**

```json
{
  "tool": "call_mutating_tools_batch",
  "arguments": {
    "operations": [
      {
        "tool": "rename_window",
        "arguments": {"window_id": "@2", "new_name": "logs"}
      },
      {
        "tool": "split_window",
        "arguments": {"window_id": "@2", "direction": "right"}
      }
    ],
    "on_error": "stop"
  }
}
```

```{fastmcp-tool-input} batch_tools.call_mutating_tools_batch
```
