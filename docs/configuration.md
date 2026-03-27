(configuration)=

# Configuration

Runtime configuration for the libtmux-mcp server. For MCP client setup, see {ref}`clients`.

## Environment variables

(LIBTMUX_SOCKET)=

### `LIBTMUX_SOCKET`

tmux socket name (`-L`). Isolates the MCP server to a specific tmux socket.

- **Type:** string
- **Default:** (none — uses the default tmux socket)

(LIBTMUX_SOCKET_PATH)=

### `LIBTMUX_SOCKET_PATH`

tmux socket path (`-S`). Alternative to socket name for custom socket locations.

- **Type:** string
- **Default:** (none)

(LIBTMUX_TMUX_BIN)=

### `LIBTMUX_TMUX_BIN`

Path to tmux binary. Useful for testing with different tmux versions.

- **Type:** string
- **Default:** `tmux`

(LIBTMUX_SAFETY)=

### `LIBTMUX_SAFETY`

Safety tier controlling which tools are available. See {ref}`safety`.

- **Type:** string
- **Default:** `mutating`
- **Values:** `readonly`, `mutating`, `destructive`

## Setting environment variables

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

By default, the MCP server connects to the default tmux socket. Set {ref}`LIBTMUX_SOCKET` to isolate AI agent activity from your personal tmux sessions:

```json
"env": { "LIBTMUX_SOCKET": "ai_workspace" }
```

The agent will only see sessions on the `ai_workspace` socket, not your personal sessions.

## All tools accept `socket_name`

Every tool accepts an optional `socket_name` parameter that overrides {ref}`LIBTMUX_SOCKET` for that call. This allows agents to work across multiple tmux servers in a single session.
