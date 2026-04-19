# Rename session

```{fastmcp-tool} session_tools.rename_session
```

**Use when** a session name no longer reflects its purpose.

**Side effects:** Renames the session. Existing references by old name will break.

**Example:**

```json
{
  "tool": "rename_session",
  "arguments": {
    "session_name": "old-name",
    "new_name": "new-name"
  }
}
```

Response:

```json
{
  "session_id": "$0",
  "session_name": "new-name",
  "window_count": 2,
  "session_attached": "0",
  "session_created": "1774521871"
}
```

```{fastmcp-tool-input} session_tools.rename_session
```
