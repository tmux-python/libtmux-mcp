# Kill server

```{fastmcp-tool} server_tools.kill_server
```

**Use when** you need to tear down the entire tmux server. This kills every
session, window, and pane.

**Avoid when** you only need to remove one session — use {tooliconl}`kill-session`.

**Side effects:** Destroys everything. Not reversible.

**Confirmation:** When the client supports MCP elicitation, the server asks
the user before killing. A decline returns an error and leaves the server
running. If the server is replaced while the user is being asked, nothing is
killed. Clients without elicitation kill without asking.

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
