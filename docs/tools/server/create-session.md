```{fastmcp-tool} server_tools.create_session
```

**Use when** you need a new isolated workspace. Sessions are the top-level
container — create one before creating windows or panes.

**Avoid when** a session with the target name already exists — check with
{tooliconl}`list-sessions` first, or the command will fail.

**Side effects:** Creates a new tmux session with one window and one pane.

**Example:**

```json
{
  "tool": "create_session",
  "arguments": {
    "session_name": "dev"
  }
}
```

Response:

```json
{
  "session_id": "$1",
  "session_name": "dev",
  "window_count": 1,
  "session_attached": "0",
  "session_created": "1774521872"
}
```

```{fastmcp-tool-input} server_tools.create_session
```
