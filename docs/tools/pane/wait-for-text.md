# Wait for text

```{fastmcp-tool} pane_tools.wait_for_text
```

**Use when** you need to block until specific output appears — waiting for a
server to start, a build to complete, or a prompt to return.

**Avoid when** the expected text may never appear — always set a reasonable
`timeout`. For repeated observation or tailing, use
{tooliconl}`capture-since`; for command completion you control, use
{tooliconl}`wait-for-channel`.

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
  "risk_band_warned": false
}
```

`risk_band_warned` is `true` when polling entered tmux's history-limit
trim-risk band. In that state, matching remains best-effort because older
scrollback can be discarded while the wait is active; use
{tooliconl}`wait-for-channel` for deterministic command completion.

```{fastmcp-tool-input} pane_tools.wait_for_text
```
