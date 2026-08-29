# Copy selection

```{fastmcp-tool} pane_tools.copy_selection
```

**Use when** a person attached to the session has highlighted something
and you are being asked about *that highlight* rather than about the
pane. This is the one case {tooliconl}`capture-pane` cannot reach: it
returns pane text, not the region a human picked out of it.

**Reads what is selected right now, not what was just copied.** A person
who pressed `Enter` or `y` has already left copy mode, and that text is
in tmux's own buffer, which this server does not read — see
{doc}`/topics/safety`. Most key bindings copy *and* cancel, so that is
the common human ending; the answer is to ask them to select again.

The selection is durable enough to act on: it survives idle time, cursor
movement and further process output. Only leaving copy mode clears it.

**Side effects:** allocates a new MCP-namespaced buffer. The selection
itself is left intact, so reading it does not disturb the person who
made it. Pass the returned `buffer_name` to {tooliconl}`paste-buffer` to
drop the selection into another pane, or {tooliconl}`delete-buffer` when
done.

**Example:**

```json
{
  "tool": "copy_selection",
  "arguments": {
    "pane_id": "%0"
  }
}
```

Response:

```json
{
  "buffer_name": "libtmux_mcp_4f3c2a1b9d8e7f6a5b4c3d2e1f0a9b8c_buf0",
  "content": "ERROR: connection refused\n  at pool.acquire (db.js:42)",
  "content_truncated": false,
  "content_truncated_lines": 0
}
```

Two refusals, both loud on purpose:

```text
pane %0 is not in copy mode
pane %0 is in copy mode but nothing is selected
```

The second matters more than it looks. tmux exits 0 for a copy with
nothing selected and creates no buffer at all, so without that check the
tool would report success and hand back a buffer name that does not
exist.

```{fastmcp-tool-input} pane_tools.copy_selection
```
