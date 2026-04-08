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
Server killed successfully
```

```{fastmcp-tool-input} server_tools.kill_server
```
