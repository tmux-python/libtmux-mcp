# Locate the caller

```{fastmcp-tool} server_tools.where_am_i
```

**Use when** you need to turn “this pane,” “current window,” or “this session”
into stable tmux IDs before another tool call. The targeted lookup costs extra
tmux round-trips and buys a live answer that does not trust stale session data
from the launch environment.

**Avoid when** you need an inventory across the server. Use
{tooliconl}`list-sessions`, {toolref}`list-windows`, or {toolref}`list-panes`
instead.

**Side effects:** None. Readonly.

The typed result separates frozen caller identity from current availability:

- **Live matching caller:** `inside_tmux` and `self_available` are `true`, with
  `pane_id`, `window_id`, and `session_id` populated.
- **Outside tmux:** `inside_tmux` is `false`, caller IDs are null, and the
  effective target still reports whether its server is running.
- **Dead target:** `server_running` and `self_available` are `false`; frozen
  caller identity remains visible when it exists.
- **Stale pane:** `pane_id` retains the frozen caller ID, while
  `self_available` is `false` and parent IDs are null.
- **Target mismatch:** caller and effective socket fields remain distinct,
  `self_available` is `false`, and no lookup crosses to the caller's server.

**Example:**

```json
{
  "tool": "where_am_i",
  "arguments": {}
}
```

Response:

```json
{
  "inside_tmux": true,
  "self_available": true,
  "pane_id": "%3",
  "window_id": "@2",
  "session_id": "$1",
  "caller_socket_path": "/path/to/tmux.sock",
  "effective_socket_name": null,
  "effective_socket_path": "/path/to/tmux.sock",
  "server_running": true,
  "safety_level": "mutating",
  "suppress_history": true
}
```

```{fastmcp-tool-input} server_tools.where_am_i
```
