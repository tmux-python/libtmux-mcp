# Kill session

```{fastmcp-tool} session_tools.kill_session
```

**Use when** you're done with a workspace and want to clean up. Kills all
windows and panes in the session.

**Avoid when** you only want to close one window — use {tooliconl}`kill-window`.

**Side effects:** Destroys the session and all its contents. Not reversible.

**Confirmation:** When the client supports MCP elicitation, the server asks
the user before killing. A decline returns an error and leaves the session in
place. If the session is replaced by a new one with the same name while the
user is being asked, nothing is killed. Clients without elicitation kill
without asking.

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
