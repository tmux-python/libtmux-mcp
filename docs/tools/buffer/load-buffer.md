# Load buffer

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
