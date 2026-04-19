# Buffers

tmux paste buffers are a server-global namespace shared by every client
on the same socket. The buffer tools in libtmux-mcp expose a narrow,
agent-namespaced subset: every allocation gets a UUID-scoped name like
`libtmux_mcp_<32-hex>_<logical>`, so concurrent agents (or parallel
tool calls from one agent) cannot collide on each other's payloads.

There is **no** `list_buffers` tool. The user's OS clipboard often syncs
into tmux paste buffers, so a generic enumeration would leak passwords,
tokens, and other private content the agent has no business reading.
Callers track the buffers they own via the {tool}`load-buffer` returns.

## Stage

```{fastmcp-tool} buffer_tools.load_buffer
```

**Use when** you need to stage multi-line text for paste — sending a
shell script, prepared input for an interactive prompt, or content
that's too long for a clean {tooliconl}`send-keys` invocation.

**Avoid when** the text is a single command line that {tooliconl}`send-keys`
can deliver directly. `load_buffer` allocates server-side state that
must be cleaned up via {tooliconl}`delete-buffer`.

**Side effects:** Allocates a tmux paste buffer. Use the returned
`buffer_name` for follow-up calls. The `content` argument is redacted
in audit logs.

**Example:**

```json
{
  "tool": "load_buffer",
  "arguments": {
    "content": "for i in 1 2 3; do\n  echo line $i\ndone\n",
    "logical_name": "loop"
  }
}
```

Response:

```json
{
  "buffer_name": "libtmux_mcp_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6_loop",
  "logical_name": "loop"
}
```

```{fastmcp-tool-input} buffer_tools.load_buffer
```

---

## Paste

```{fastmcp-tool} buffer_tools.paste_buffer
```

**Use when** you've staged content via {tooliconl}`load-buffer` and
need to push it into a target pane. Use bracketed paste mode
(default `bracket=true`) for terminals that handle it; the wrapping
escape sequences signal "this is pasted text, not typed input".

**Avoid when** the buffer name doesn't match the MCP namespace —
`paste_buffer` refuses non-`libtmux_mcp_*` names so it cannot be
turned into an arbitrary-buffer paster.

**Side effects:** Pastes content into the target pane (the pane's
shell receives the bytes as input). Open-world: the resulting shell
behavior is whatever the pasted bytes invoke.

```{fastmcp-tool-input} buffer_tools.paste_buffer
```

---

## Inspect

```{fastmcp-tool} buffer_tools.show_buffer
```

**Use when** you need to confirm what was staged before pasting, or
to read back a buffer between modifications. Restricted to
MCP-namespaced buffers — non-agent buffers are rejected.

**Side effects:** None. Readonly.

```{fastmcp-tool-input} buffer_tools.show_buffer
```

---

## Clean up

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
