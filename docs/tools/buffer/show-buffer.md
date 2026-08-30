# Show buffer

```{fastmcp-tool} buffer_tools.show_buffer
```

**Use when** you need to confirm what was staged before pasting, or
to read back a buffer between modifications. Restricted to
MCP-namespaced buffers — non-agent buffers are rejected.

**Side effects:** The built-in request reads data only. A configured alias or
hook can add effects; see {ref}`trust`.

```{fastmcp-tool-input} buffer_tools.show_buffer
```

---

## Clean up
