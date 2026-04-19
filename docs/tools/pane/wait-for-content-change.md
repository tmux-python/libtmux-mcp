# Wait for content change

```{fastmcp-tool} pane_tools.wait_for_content_change
```

**Use when** you've sent a command and need to wait for *something* to happen,
but you don't know what the output will look like. Unlike
{tooliconl}`wait-for-text`, this waits for *any* screen change rather than a
specific pattern.

**Avoid when** you know the expected output — {tooliconl}`wait-for-text` is more
precise and avoids false positives from unrelated output.

**Side effects:** None. Readonly. Blocks until content changes or timeout.

**Example:**

```json
{
  "tool": "wait_for_content_change",
  "arguments": {
    "pane_id": "%0",
    "timeout": 10
  }
}
```

Response:

```json
{
  "changed": true,
  "pane_id": "%0",
  "elapsed_seconds": 1.234,
  "timed_out": false
}
```

```{fastmcp-tool-input} pane_tools.wait_for_content_change
```
