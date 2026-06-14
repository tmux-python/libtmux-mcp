# Call destructive tools batch

```{fastmcp-tool} batch_tools.call_destructive_tools_batch
```

**Use when** a reviewed workflow intentionally includes destructive
tools and should still return one per-operation result envelope.

**Avoid when** the workflow can fit inside
{tooliconl}`call-mutating-tools-batch`. This wrapper can invoke
destructive nested tools when the server safety tier permits them.

**Side effects:** Runs readonly, mutating, and destructive nested tools
in order. Recursive batch calls are rejected.

**Example:**

```json
{
  "tool": "call_destructive_tools_batch",
  "arguments": {
    "operations": [
      {"tool": "kill_pane", "arguments": {"pane_id": "%7"}},
      {"tool": "list_panes", "arguments": {"window_id": "@3"}}
    ],
    "on_error": "stop"
  }
}
```

```{fastmcp-tool-input} batch_tools.call_destructive_tools_batch
```
