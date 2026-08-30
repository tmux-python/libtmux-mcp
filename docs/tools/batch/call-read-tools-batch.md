# Call Read Tools Batch

```{fastmcp-tool} batch_tools.call_read_tools_batch
```

**Use when** you need several `inspect` operations in one ordered MCP turn,
such as listing sessions and then reading server metadata.

**Avoid when** any nested operation changes tmux state — call anything that
writes directly. Call the tools individually when each result should be
reviewed before choosing the next action.

**Side effects:** Calls only `inspect` tools. Each nested built-in request reads
data only; a configured alias or hook can add effects. Anything outside that
toolset is rejected, whatever this server has enabled. A batch gives every
nested call the wrapper's name, so a client rule keyed on the inner tool would
not fire. See {ref}`trust`.

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
