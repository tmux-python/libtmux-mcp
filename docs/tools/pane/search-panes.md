# Search panes

```{fastmcp-tool} pane_tools.search_panes
```

**Use when** you need to find specific text across multiple panes — locating
which pane has an error, finding a running process, or checking output
without knowing which pane to look in.

**Avoid when** you already know the target pane — use {tooliconl}`capture-pane`
for a one-shot read, or {tooliconl}`capture-since` for repeated observation.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "search_panes",
  "arguments": {
    "pattern": "FAIL",
    "session_name": "dev"
  }
}
```

Response is a {class}`~libtmux_mcp.models.SearchPanesResult` wrapper: the matching panes live under
`matches`, and the wrapper fields (`truncated`, `truncated_panes`,
`total_panes_matched`, `offset`, `limit`) support pagination. For larger
result sets, iterate by re-calling with `offset += len(matches)`; stop when
`truncated == false` and `truncated_panes == []`.

Plain text searches with no content range use tmux's fast visual-row search.
That path is quick, but it cannot match text split by terminal wrapping. Pass
`regex=true` or a `content_start` / `content_end` range when long build,
test, or log lines may cross the pane's wrap column.

:::{note} Migrating from the flat-list shape
Earlier alpha releases returned a bare list of
{class}`~libtmux_mcp.models.PaneContentMatch` rows.
Clients iterating the old shape directly (e.g. `for m in search_panes(...)`)
must switch to `for m in search_panes(...).matches`. See the
[CHANGES](../../../CHANGES) entry for context.
:::

```json
{
  "matches": [
    {
      "pane_id": "%0",
      "pane_current_command": "zsh",
      "pane_current_path": "/home/user/myproject",
      "window_id": "@0",
      "window_name": "editor",
      "session_id": "$0",
      "session_name": "dev",
      "matched_lines": [
        "FAIL: test_upload (AssertionError)",
        "3 tests: 2 passed, 1 failed"
      ],
      "is_caller": false
    }
  ],
  "truncated": false,
  "truncated_panes": [],
  "total_panes_matched": 1,
  "offset": 0,
  "limit": 500
}
```

```{fastmcp-tool-input} pane_tools.search_panes
```
