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
    "patterns": ["Server listening"],
    "stop": ["Address already in use"],
    "pane_id": "%2",
    "timeout": 30
  }
}
```

`patterns` is a list; pass `null` to wait for any new output at all. A `stop`
entry is a failure marker — a hit ends the wait immediately with
`outcome="stopped"`.

Response:

```json
{
  "found": true,
  "outcome": "matched",
  "matched_index": 0,
  "matched_lines": [
    "Server listening on port 8000"
  ],
  "tail": [
    "Server listening on port 8000"
  ],
  "saw_new_output": true,
  "matched_at_entry": false,
  "alternate_screen": false,
  "pane_id": "%2",
  "elapsed_seconds": 0.002,
  "effective_timeout": 30.0
}
```

`outcome` states how the wait ended: `matched`, `any_output`, `stopped`,
`alternate_screen`, or `timeout`. `matched_index` names which `patterns` or
`stop` entry fired. `matched_at_entry` is `true` when a pattern was already on
screen when the wait began and was excluded as stale paint.

`effective_timeout` is the timeout actually enforced. An over-large `timeout` is
clamped to the server ceiling (`LIBTMUX_MCP_WAIT_MAX_SECONDS`, 30 s by default)
rather than rejected, so this can be lower than what you asked for.

Matching is best-effort once polling enters tmux's history-limit trim-risk band,
because older scrollback can be discarded while the wait is active. The server
reports that as an MCP warning notification; use {tooliconl}`wait-for-channel`
for deterministic command completion.

```{fastmcp-tool-input} pane_tools.wait_for_text
```
