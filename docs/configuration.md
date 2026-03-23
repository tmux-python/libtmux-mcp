(configuration)=

# Configuration

Runtime configuration for the libtmux-mcp server. For MCP client setup, see {ref}`clients`.

## Environment variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LIBTMUX_SOCKET` | string | (none) | tmux socket name (`-L`). Isolates the MCP server to a specific tmux socket. |
| `LIBTMUX_SOCKET_PATH` | string | (none) | tmux socket path (`-S`). Alternative to socket name for custom socket locations. |
| `LIBTMUX_TMUX_BIN` | string | `tmux` | Path to tmux binary. Useful for testing with different tmux versions. |
| `LIBTMUX_SAFETY` | string | `mutating` | Safety tier: `readonly`, `mutating`, or `destructive`. See {ref}`safety`. |

Set environment variables in your MCP client config:

```json
{
    "mcpServers": {
        "libtmux": {
            "command": "uvx",
            "args": ["libtmux-mcp"],
            "env": {
                "LIBTMUX_SOCKET": "ai_workspace",
                "LIBTMUX_SAFETY": "readonly"
            }
        }
    }
}
```

## Socket isolation

By default, the MCP server connects to the default tmux socket. Set `LIBTMUX_SOCKET` to isolate AI agent activity from your personal tmux sessions:

```json
"env": { "LIBTMUX_SOCKET": "ai_workspace" }
```

The agent will only see sessions on the `ai_workspace` socket, not your personal sessions.

## All tools accept `socket_name`

Every tool accepts an optional `socket_name` parameter that overrides `LIBTMUX_SOCKET` for that call. This allows agents to work across multiple tmux servers in a single session.
