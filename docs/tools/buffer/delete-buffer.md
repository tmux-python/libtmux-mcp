# Delete buffer

```{fastmcp-tool} buffer_tools.delete_buffer
```

**Use when** you're done with a buffer and want to free server-side
state. Always call this when the buffer's purpose is complete —
tmux servers outlive the MCP process, so leaked buffers persist
across MCP restarts.

**Side effects:** Removes the named buffer from the tmux server.
Subsequent {tooliconl}`paste-buffer` calls against the deleted name
return `ToolError`.

```{fastmcp-tool-input} buffer_tools.delete_buffer
```
