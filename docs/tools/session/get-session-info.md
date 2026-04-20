# Get session info

```{fastmcp-tool} session_tools.get_session_info
```

**Use when** you need metadata for a single session (ID, name, window
count, attachment status, activity timestamp) and you already know its
`session_id` or `session_name`. Avoids the `list_sessions` + filter dance.

**Avoid when** you need every session — call `list_sessions` or iterate
via the `tmux://sessions` resource.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "get_session_info",
  "arguments": {
    "session_id": "$0"
  }
}
```

Response:

```json
{
  "session_id": "$0",
  "session_name": "dev",
  "window_count": 3,
  "session_attached": "1",
  "session_created": "1713600000",
  "active_pane_id": "%0"
}
```

Resolve by name when only the session_name is known:

```json
{
  "tool": "get_session_info",
  "arguments": {
    "session_name": "dev"
  }
}
```

```{fastmcp-tool-input} session_tools.get_session_info
```
