```{fastmcp-tool} pane_tools.send_keys
```

**Use when** you need to type commands, press keys, or interact with a
terminal. This is the primary way to execute commands in tmux panes.

**Avoid when** you need to run something and immediately capture the result —
send keys first, then use {tooliconl}`capture-pane` or {tooliconl}`wait-for-text`.

**Side effects:** Sends keystrokes to the pane. If `enter` is true (default),
the command executes.

**Example:**

```json
{
  "tool": "send_keys",
  "arguments": {
    "keys": "npm start",
    "pane_id": "%2"
  }
}
```

Response (string):

```text
Keys sent to pane %2
```

```{fastmcp-tool-input} pane_tools.send_keys
```
