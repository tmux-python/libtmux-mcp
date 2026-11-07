# Kill pane

```{fastmcp-tool} pane_tools.kill_pane
```

**Use when** you're done with a specific terminal and want to remove it
without affecting sibling panes.

**Avoid when** you want to remove the entire window — use {tooliconl}`kill-window`.

**Side effects:** Destroys the pane. Not reversible.

**Confirmation:** When the client supports MCP elicitation, the server asks
the user before killing. A decline returns an error and leaves the pane in
place. If the pane is replaced while the user is being asked, nothing is
killed. Clients without elicitation kill without asking.

**Example:**

```json
{
  "tool": "kill_pane",
  "arguments": {
    "pane_id": "%1"
  }
}
```

Response (string):

```text
Pane killed: %1
```

```{fastmcp-tool-input} pane_tools.kill_pane
```
