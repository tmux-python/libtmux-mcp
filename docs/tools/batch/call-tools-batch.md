# Call tools batch

```{fastmcp-tool} batch_tools.call_tools_batch
```

**Use when** you need an ordered workflow made from existing typed MCP
tools, such as renaming and splitting a known window, while preserving
each tool's own schema and safety checks.

**Avoid when** the steps are tmux pane or window operations; prefer the
typed {tooliconl}`run-tmux-plan` tool. For shell commands with completion
and output, prefer {tooliconl}`run-command`.

**Safety:** Each nested call still runs through the server's safety tier,
so the batch can never run a nested tool the tier hides. Set `max_tier` to
cap the batch below the server tier: `readonly` refuses any mutating or
destructive nested call, and `mutating` refuses destructive ones. The
default permits every tier the server already allows.

**Example:**

```json
{
  "tool": "call_tools_batch",
  "arguments": {
    "operations": [
      {"tool": "rename_window",
       "arguments": {"window_id": "@2", "new_name": "logs"}},
      {"tool": "split_window",
       "arguments": {"window_id": "@2", "direction": "right"}}
    ],
    "max_tier": "mutating",
    "on_error": "stop"
  }
}
```

```{fastmcp-tool-input} batch_tools.call_tools_batch
```
