# Send keys

```{fastmcp-tool} pane_tools.send_keys
```

**Use when** you need to type raw input, press keys, or interact with
a terminal program. For several ordered raw-input operations, use
{tooliconl}`send-keys-batch`.

**Avoid when** you need to run one authored shell command and
immediately capture its result — use {tooliconl}`run-command` so exit
status, timeout state, and output come back as one typed result. For
output the agent does not author, use {tooliconl}`wait-for-text` or
observe with {tooliconl}`capture-since`.

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
