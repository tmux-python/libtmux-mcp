```{fastmcp-tool} pane_tools.search_panes
```

**Use when** you need to find specific text across multiple panes — locating
which pane has an error, finding a running process, or checking output
without knowing which pane to look in.

**Avoid when** you already know the target pane — use {tooliconl}`capture-pane`
directly.

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

Response:

```json
[
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
    "is_caller": null
  }
]
```

```{fastmcp-tool-input} pane_tools.search_panes
```
