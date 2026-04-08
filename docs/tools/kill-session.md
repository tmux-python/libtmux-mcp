```{fastmcp-tool} session_tools.kill_session
```

**Use when** you're done with a workspace and want to clean up. Kills all
windows and panes in the session.

**Avoid when** you only want to close one window — use {tooliconl}`kill-window`.

**Side effects:** Destroys the session and all its contents. Not reversible.

**Example:**

```json
{
  "tool": "kill_session",
  "arguments": {
    "session_name": "old-workspace"
  }
}
```

Response (string):

```text
Session killed: old-workspace
```

```{fastmcp-tool-input} session_tools.kill_session
```
