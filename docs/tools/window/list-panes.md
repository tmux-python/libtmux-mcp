# List panes

```{fastmcp-tool} window_tools.list_panes
```

**Use when** you need to discover which panes exist in a window before
sending keys or capturing output.

**Side effects:** None. Readonly.

{tooliconl}`list-panes` keeps `scope="server"` as its default. With no
session or window selector, that lists panes across the effective server;
existing selectors still narrow the result. Set `scope="caller_session"`
to list every pane in the session containing the frozen caller pane.
That scope performs an extra targeted tmux lookup; the added work buys
fail-closed live-session accuracy instead of trusting stale session metadata.

Caller-session scope cannot be combined with `session_name`, `session_id`,
`window_id`, or `window_index`. It raises a tool error instead of widening
the search when the MCP invocation started outside tmux, the caller pane no
longer resolves, or the caller socket differs from the effective target.

Results arrive as a {class}`~libtmux_mcp.models.PanePage`. The default
`detail="summary"` rows carry pane and parent identity, active and caller
state, title, and current command. Set `detail="full"` when you need geometry,
the working directory, TTY, or process metadata; the larger projection is
useful for targeted inspection. Filters run against full pane metadata before
rows are projected, sorted, and paged. The default page uses `limit=100` and
`offset=0`.

**Example:**

```json
{
  "tool": "list_panes",
  "arguments": {
    "session_name": "dev"
  }
}
```

Response:

```json
{
  "items": [
    {
      "pane_id": "%0",
      "pane_index": "0",
      "pane_current_command": "zsh",
      "pane_title": "build",
      "pane_active": "1",
      "window_id": "@0",
      "session_id": "$0",
      "is_caller": false
    },
    {
      "pane_id": "%1",
      "pane_index": "1",
      "pane_current_command": "zsh",
      "pane_title": "",
      "pane_active": "0",
      "window_id": "@0",
      "session_id": "$0",
      "is_caller": false
    }
  ],
  "total": 2,
  "offset": 0,
  "limit": 100,
  "truncated": false
}
```

```{fastmcp-tool-input} window_tools.list_panes
```

## Act
