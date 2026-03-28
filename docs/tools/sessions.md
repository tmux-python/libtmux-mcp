# Sessions

## Inspect

```{fastmcp-tool} server_tools.list_sessions
```

**Use when** you need session names, IDs, or attached status before deciding
which session to target.

**Avoid when** you need window or pane details — use {tooliconl}`list-windows` or
{tooliconl}`list-panes` instead.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "list_sessions",
  "arguments": {}
}
```

Response:

```json
[
  {
    "session_id": "$0",
    "session_name": "myproject",
    "window_count": 2,
    "session_attached": "0",
    "session_created": "1774521871"
  }
]
```

```{fastmcp-tool-input} server_tools.list_sessions
```

---

```{fastmcp-tool} server_tools.get_server_info
```

**Use when** you need to verify the tmux server is running, check its PID,
or inspect server-level state before creating sessions.

**Avoid when** you only need session names — use {tooliconl}`list-sessions`.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "get_server_info",
  "arguments": {}
}
```

Response:

```json
{
  "is_alive": true,
  "socket_name": null,
  "socket_path": null,
  "session_count": 2,
  "version": "3.6a"
}
```

```{fastmcp-tool-input} server_tools.get_server_info
```

## Act

```{fastmcp-tool} server_tools.create_session
```

**Use when** you need a new isolated workspace. Sessions are the top-level
container — create one before creating windows or panes.

**Avoid when** a session with the target name already exists — check with
{tooliconl}`list-sessions` first, or the command will fail.

**Side effects:** Creates a new tmux session. Attaches if `attach` is true.

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

---

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

## Destroy

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

---

```{fastmcp-tool} server_tools.kill_server
```

**Use when** you need to tear down the entire tmux server. This kills every
session, window, and pane.

**Avoid when** you only need to remove one session — use {tooliconl}`kill-session`.

**Side effects:** Destroys everything. Not reversible.

**Example:**

```json
{
  "tool": "kill_server",
  "arguments": {}
}
```

Response (string):

```text
Server killed
```

```{fastmcp-tool-input} server_tools.kill_server
```
