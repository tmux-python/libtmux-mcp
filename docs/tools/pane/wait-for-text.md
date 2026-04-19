# Wait for text

```{fastmcp-tool} pane_tools.wait_for_text
```

**Use when** you need to block until specific output appears — waiting for a
server to start, a build to complete, or a prompt to return.

**Avoid when** the expected text may never appear — always set a reasonable
`timeout`. For known output, {tooliconl}`capture-pane` after a known delay
may suffice, but `wait_for_text` is preferred because it adapts to variable
timing.

**Side effects:** None. Readonly. Blocks until text appears or timeout.

**Example:**

```json
{
  "tool": "wait_for_text",
  "arguments": {
    "pattern": "Server listening",
    "pane_id": "%2",
    "timeout": 30
  }
}
```

Response:

```json
{
  "found": true,
  "matched_lines": [
    "Server listening on port 8000"
  ],
  "pane_id": "%2",
  "elapsed_seconds": 0.002,
  "timed_out": false
}
```

```{fastmcp-tool-input} pane_tools.wait_for_text
```
