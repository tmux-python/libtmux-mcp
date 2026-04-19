# Paste buffer

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
