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
[
  {
    "pane_id": "%0",
    "pane_index": "0",
    "pane_width": "80",
    "pane_height": "15",
    "pane_current_command": "zsh",
    "pane_current_path": "/home/user/myproject",
    "pane_pid": "12345",
    "pane_title": "build",
    "pane_active": "1",
    "window_id": "@0",
    "session_id": "$0",
    "is_caller": false
  },
  {
    "pane_id": "%1",
    "pane_index": "1",
    "pane_width": "80",
    "pane_height": "8",
    "pane_current_command": "zsh",
    "pane_current_path": "/home/user/myproject",
    "pane_pid": "12400",
    "pane_title": "",
    "pane_active": "0",
    "window_id": "@0",
    "session_id": "$0",
    "is_caller": false
  }
]
```

```{fastmcp-tool-input} window_tools.list_panes
```

## Act
