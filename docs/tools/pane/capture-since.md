# Capture since

```{fastmcp-tool} pane_tools.capture_since
```

**Use when** you need to observe the same pane repeatedly — tailing logs,
watching a long-running command, checking a daemon, or revisiting a terminal
without paying to re-read the same scrollback every turn. The first call returns
the current visible screen plus a cursor; later calls pass that cursor back and
receive only rows written or rewritten after it.

**Avoid when** you control the command and only need completion — use
{tooliconl}`run-command`, which waits and returns exit status plus
output in one typed result. If you need a one-shot content + metadata
view, use {tooliconl}`snapshot-pane`; if you do not know which pane
contains text, use {tooliconl}`search-panes`.

**Side effects:** None. Readonly.

**Example:**

Start a cursor with the currently visible screen:

```json
{
  "tool": "capture_since",
  "arguments": {
    "pane_id": "%2"
  }
}
```

Response:

```json
{
  "pane_id": "%2",
  "cursor": "capture-since-v1:...",
  "lines": [
    "$ pytest -vv",
    "tests/test_api.py::test_health PASSED"
  ],
  "elapsed_seconds": 0.003,
  "lines_missed": false,
  "truncated": false,
  "truncated_lines": 0,
  "truncated_bytes": 0
}
```

Read only content since that cursor:

```json
{
  "tool": "capture_since",
  "arguments": {
    "cursor": "capture-since-v1:..."
  }
}
```

The cursor carries the original pane id, so the follow-up call does not need
`pane_id`. If you pass both, they must match; a cursor for another pane raises
an {exc}`~libtmux_mcp._utils.ExpectedToolError` instead of silently reading the
wrong process.

If nothing new was written after the cursor, `lines` is empty and the response
still includes a fresh cursor for the same pane. If the cursor row scrolled into
retained history, the tool can still return an exact delta; retained scrollback
is not a loss condition.

`lines_missed` becomes `true` when tmux has cleared or trimmed the history
needed to compute an exact delta. In that case, `lines` is a conservative
current visible capture and the response includes a fresh cursor.

Pane lifecycle is part of the cursor contract. If the pane dies or is respawned,
the call raises an {exc}`~libtmux_mcp._utils.ExpectedToolError` instead of
reading from a different process that reused the same pane id.

`truncated`, `truncated_lines`, and `truncated_bytes` are structured metadata.
No truncation marker is injected into `lines`, so clients can display terminal
text without parsing an in-band header.

The cursor is intentionally opaque. It is based on tmux grid state
(`history_size + cursor_y`) and pane lifecycle fields (`pane_id`, `pane_pid`);
see tmux's grid and capture implementation in
[grid.c](https://github.com/tmux/tmux/blob/134ba6c/grid.c) and
[cmd-capture-pane.c](https://github.com/tmux/tmux/blob/134ba6c/cmd-capture-pane.c),
and libtmux's
{meth}`~libtmux.Pane.capture_pane`.

```{fastmcp-tool-input} pane_tools.capture_since
```
