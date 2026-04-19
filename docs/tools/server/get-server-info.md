# Get server info

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
