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

### Tools

| Module | Tools |
|--------|-------|
| **Server** | `list_sessions`, `create_session`, `kill_server`, `get_server_info` |
| **Session** | `list_windows`, `create_window`, `rename_session`, `kill_session` |
| **Window** | `list_panes`, `split_window`, `rename_window`, `kill_window`, `select_layout`, `resize_window` |
| **Pane** | `send_keys`, `capture_pane`, `resize_pane`, `kill_pane`, `set_pane_title`, `get_pane_info`, `clear_pane`, `search_panes`, `wait_for_text` |
| **Options** | `show_option`, `set_option` |
| **Environment** | `show_environment`, `set_environment` |

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
