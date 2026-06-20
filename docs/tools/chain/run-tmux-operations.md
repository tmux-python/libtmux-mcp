# Run tmux operations

```{fastmcp-tool} chain_tools.run_tmux_operations
```

**Use when** you need several typed tmux operations to run in order and
want libtmux-mcp to fold safe no-output steps into one native tmux
sequence.

**Avoid when** you need to call arbitrary MCP tools; use
{tooliconl}`call-mutating-tools-batch` for that. Use individual tools
when a workflow has only one step.

**Side effects:** Mutates tmux state according to the submitted
operation list. With `on_error="stop"`, chainable operations may share
one tmux sequence and native tmux failure semantics stop later steps.
With `on_error="continue"`, operations run as standalone dispatches so
later steps can still run after an earlier failure.

**Example:**

```json
{
  "tool": "run_tmux_operations",
  "arguments": {
    "operations": [
      {"kind": "split_pane", "pane_id": "%1", "ref": "work"},
      {"kind": "send_keys", "pane_ref": "work", "keys": "uv run pytest"}
    ],
    "on_error": "stop"
  }
}
```

```{fastmcp-tool-input} chain_tools.run_tmux_operations
```
