(about)=

# About

libtmux-mcp is an MCP (Model Context Protocol) server that gives AI agents programmatic control over tmux sessions. It is built on [libtmux](https://libtmux.git-pull.com/), a typed Python library for tmux.

## Architecture

```
src/libtmux_mcp/
    __init__.py           # Entry point: main()
    __main__.py           # python -m libtmux_mcp support
    server.py             # FastMCP instance and configuration
    _utils.py             # Server caching, resolvers, serializers, error handling
    models.py             # Pydantic output models
    middleware.py         # Safety tier middleware
    tools/
        server_tools.py   # list_sessions, create_session, kill_server, get_server_info
        session_tools.py  # list_windows, create_window, rename_session, kill_session
        window_tools.py   # list_panes, split_window, rename_window, kill_window, select_layout, resize_window
        pane_tools.py     # send_keys, capture_pane, resize_pane, kill_pane, set_pane_title, get_pane_info, clear_pane, search_panes, wait_for_text
        option_tools.py   # show_option, set_option
        env_tools.py      # show_environment, set_environment
    resources/
        hierarchy.py      # tmux:// URI resources
```

## tmux hierarchy

libtmux-mcp mirrors the tmux object hierarchy:

```
Server (tmux server instance)
  └─ Session (tmux session)
      └─ Window (tmux window)
          └─ Pane (tmux pane)
```

## Tools

All tools accept an optional `socket_name` (string) parameter for multi-server support. It defaults to the `LIBTMUX_SOCKET` env var. This parameter is omitted from the listings below.

### Server tools

- **`list_sessions`** — List all sessions
  - `filters` (dict or JSON string): Django-style filters (e.g. `{"session_name__contains": "dev"}`)
- **`create_session`** — Create a new session
  - `session_name` (string): Name for the session
  - `window_name` (string): Name for the initial window
  - `start_directory` (string): Working directory
  - `x` (integer): Initial width
  - `y` (integer): Initial height
  - `environment` (dict): Environment variables to set
- **`get_server_info`** — Get server status and metadata
- **`kill_server`** — Kill the tmux server (destructive)

### Session tools

- **`list_windows`** — List windows in a session (or all sessions)
  - `session_name` (string): Session name
  - `session_id` (string): Session ID (e.g. `$1`)
  - `filters` (dict or JSON string): Django-style filters
- **`create_window`** — Create a new window
  - `session_name` / `session_id` (string): Target session
  - `window_name` (string): Name for the window
  - `start_directory` (string): Working directory
  - `attach` (boolean, default: false): Make the window active
  - `direction` (`"before"` or `"after"`): Placement relative to current window
- **`rename_session`** — Rename a session
  - `new_name` (string, required): New session name
  - `session_name` / `session_id` (string): Target session
- **`kill_session`** — Kill a session (destructive)
  - `session_name` / `session_id` (string): Target session

### Window tools

- **`list_panes`** — List panes in a window (or all windows)
  - `session_name` / `session_id` (string): Target session
  - `window_id` (string): Window ID (e.g. `@1`)
  - `window_index` (string): Window index within session
  - `filters` (dict or JSON string): Django-style filters
- **`split_window`** — Split a window to create a new pane
  - `pane_id` (string): Pane to split from
  - `session_name` / `session_id` / `window_id` / `window_index` (string): Target
  - `direction` (`"above"`, `"below"`, `"left"`, `"right"`): Split direction
  - `size` (string or integer): Size (e.g. `"50%"` or lines/columns)
  - `start_directory` (string): Working directory
  - `shell` (string): Shell command to run
- **`rename_window`** — Rename a window
  - `new_name` (string, required): New window name
  - `window_id` / `window_index` (string): Target window
  - `session_name` / `session_id` (string): Target session
- **`kill_window`** — Kill a window (destructive)
  - `window_id` (string, required): Window ID
- **`select_layout`** — Set window layout
  - `layout` (string, required): `even-horizontal`, `even-vertical`, `main-horizontal`, `main-vertical`, `tiled`, or custom layout string
  - `window_id` / `window_index` (string): Target window
  - `session_name` / `session_id` (string): Target session
- **`resize_window`** — Resize a window
  - `window_id` / `window_index` (string): Target window
  - `session_name` / `session_id` (string): Target session
  - `height` (integer): New height in lines
  - `width` (integer): New width in columns

### Pane tools

- **`send_keys`** — Send keys or text to a pane
  - `keys` (string, required): Keys or text to send
  - `pane_id` (string): Target pane ID (e.g. `%1`)
  - `session_name` / `session_id` / `window_id` (string): Fallback targeting
  - `enter` (boolean, default: true): Press Enter after sending
  - `literal` (boolean, default: false): Send literally (no tmux key interpretation)
  - `suppress_history` (boolean, default: false): Prepend space to suppress shell history
- **`capture_pane`** — Capture pane content as text
  - `pane_id` / `session_name` / `session_id` / `window_id` (string): Target
  - `start` (integer): Start line (negative = scrollback)
  - `end` (integer): End line
- **`get_pane_info`** — Get pane metadata (size, title, current command)
  - `pane_id` / `session_name` / `session_id` / `window_id` (string): Target
- **`set_pane_title`** — Set a pane's title
  - `title` (string, required): New title
  - `pane_id` / `session_name` / `session_id` / `window_id` (string): Target
- **`resize_pane`** — Resize a pane
  - `pane_id` / `session_name` / `session_id` / `window_id` (string): Target
  - `height` (integer): New height in lines
  - `width` (integer): New width in columns
  - `zoom` (boolean): Toggle zoom (cannot combine with height/width)
- **`clear_pane`** — Clear pane content and scrollback
  - `pane_id` / `session_name` / `session_id` / `window_id` (string): Target
- **`search_panes`** — Search across all pane contents
  - `pattern` (string, required): Text or regex to search for
  - `regex` (boolean, default: false): Interpret pattern as regex
  - `match_case` (boolean, default: false): Case-sensitive matching
  - `session_name` / `session_id` (string): Limit to a session
  - `content_start` / `content_end` (integer): Line range for capture
- **`wait_for_text`** — Wait for text to appear in a pane
  - `pattern` (string, required): Text or regex to wait for
  - `regex` (boolean, default: false): Interpret pattern as regex
  - `pane_id` / `session_name` / `session_id` / `window_id` (string): Target
  - `timeout` (number, default: 8.0): Maximum seconds to wait
  - `interval` (number, default: 0.05): Seconds between polls
  - `match_case` (boolean, default: false): Case-sensitive matching
  - `content_start` / `content_end` (integer): Line range for capture
- **`kill_pane`** — Kill a pane (destructive)
  - `pane_id` (string, required): Pane ID

### Option tools

- **`show_option`** — Query a tmux option value
  - `option` (string, required): Option name
  - `scope` (`"server"`, `"session"`, `"window"`, `"pane"`): Option scope
  - `target` (string): Target identifier (session name, window ID, or pane ID)
  - `global_` (boolean, default: false): Query the global value
- **`set_option`** — Set a tmux option
  - `option` (string, required): Option name
  - `value` (string, required): Value to set
  - `scope` / `target` / `global_`: Same as `show_option`

### Environment tools

- **`show_environment`** — Show tmux environment variables
  - `session_name` / `session_id` (string): Target session (omit for server-level)
- **`set_environment`** — Set a tmux environment variable
  - `name` (string, required): Variable name
  - `value` (string, required): Variable value
  - `session_name` / `session_id` (string): Target session (omit for server-level)

## Resources

### `tmux://` URI resources

- `tmux://sessions` — All sessions
- `tmux://sessions/{name}` — Session detail with windows
- `tmux://sessions/{name}/windows` — Session's windows
- `tmux://sessions/{name}/windows/{index}` — Window detail with panes
- `tmux://panes/{id}` — Pane details
- `tmux://panes/{id}/content` — Pane captured content

## Safety tiers

Control which tools are available via `LIBTMUX_SAFETY` env var:

| Tier | Access | Use case |
|------|--------|----------|
| `readonly` | List, capture, search, info | Monitoring, browsing |
| `mutating` (default) | + create, send_keys, rename, resize | Normal agent workflow |
| `destructive` | + kill_server, kill_session, kill_window, kill_pane | Full control |

The safety middleware:
- **Hides** tools above the configured tier from tool listings
- **Blocks** execution of tools above the tier with clear error messages
- **Fail-closed**: tools without a recognized tier tag are denied

## Agent self-awareness

When the MCP server runs inside a tmux pane (detected via `TMUX_PANE` env var), it includes the caller's pane context in server instructions and annotates the caller's own pane with `is_caller=true` in tool results. This prevents agents from accidentally killing their own pane.

## Server caching

Server instances are cached by `(socket_name, socket_path, tmux_bin)` tuple with thread-safe access. Dead servers are automatically evicted via `is_alive()` checks.

## References

- [libtmux](https://libtmux.git-pull.com/) — Core tmux Python library
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [MCP Specification](https://modelcontextprotocol.io/) — Model Context Protocol
- [tmux man page](http://man.openbsd.org/OpenBSD-current/man1/tmux.1)
