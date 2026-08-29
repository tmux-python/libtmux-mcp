# Call Read Tools Batch

```{fastmcp-tool} batch_tools.call_read_tools_batch
```

**Use when** you need several read-only observations in one ordered
MCP turn, such as listing sessions and then reading server metadata.

**Avoid when** any nested operation changes tmux state — use
a direct call for anything that writes
workflows, or call the individual tools when each result should be
reviewed before choosing the next action.

**Side effects:** None beyond the nested `inspect` tools. Anything outside
that toolset is rejected, whatever this server has enabled — a batch gives
every nested call the wrapper's name, so a client rule keyed on the inner
tool would not fire.

**Example:**

```json
{
  "tool": "call_read_tools_batch",
  "arguments": {
    "operations": [
      {"tool": "list_sessions", "arguments": {}},
      {"tool": "get_server_info", "arguments": {}}
    ],
    "on_error": "stop"
  }
}
```

```{fastmcp-tool-input} batch_tools.call_read_tools_batch
```
