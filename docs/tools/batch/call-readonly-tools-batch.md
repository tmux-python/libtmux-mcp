# Call readonly tools batch

```{fastmcp-tool} batch_tools.call_readonly_tools_batch
```

**Use when** you need several read-only observations in one ordered
MCP turn, such as listing sessions and then reading server metadata.

**Avoid when** any nested operation changes tmux state — use
{tooliconl}`call-mutating-tools-batch` for readonly + mutating
workflows, or call the individual tools when each result should be
reviewed before choosing the next action.

**Side effects:** None beyond the nested readonly tools. Mutating and
destructive nested tools are rejected even when the server process is
running with a higher safety tier.

**Example:**

```json
{
  "tool": "call_readonly_tools_batch",
  "arguments": {
    "operations": [
      {"tool": "list_sessions", "arguments": {}},
      {"tool": "get_server_info", "arguments": {}}
    ],
    "on_error": "stop"
  }
}
```

```{fastmcp-tool-input} batch_tools.call_readonly_tools_batch
```
