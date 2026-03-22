(safety)=

# Safety tiers

libtmux-mcp uses a three-tier safety system to control which tools are available to AI agents.

## Overview

| Tier | Access | Use case |
|------|--------|----------|
| `readonly` | List, capture, search, info | Monitoring, browsing |
| `mutating` (default) | + create, send_keys, rename, resize | Normal agent workflow |
| `destructive` | + kill_server, kill_session, kill_window, kill_pane | Full control |

## Configuration

Set the safety tier via the `LIBTMUX_SAFETY` environment variable:

```json
{
    "mcpServers": {
        "libtmux": {
            "command": "uvx",
            "args": ["libtmux-mcp"],
            "env": {
                "LIBTMUX_SAFETY": "readonly"
            }
        }
    }
}
```

## How it works

### Dual-layer gating

1. **FastMCP tag visibility**: Tools are tagged with their tier. Only tags at or below the configured tier are enabled via `mcp.enable(tags=..., only=True)`.

2. **Safety middleware**: A secondary middleware layer hides tools from listings and blocks execution with clear error messages if a tool above the tier is somehow invoked.

### Tool tags

Every tool is tagged with exactly one safety tier:

- `readonly` — Read-only operations that don't modify tmux state
- `mutating` — Operations that create, modify, or send input to tmux objects
- `destructive` — Operations that destroy tmux objects (kill commands)

### Fail-closed design

Tools without a recognized tier tag are **denied by default**. This prevents accidentally exposing new tools without explicit safety classification.

## Self-kill protection

Destructive tools include safeguards against self-harm:

- `kill_server` refuses to run if the MCP server is inside the target server
- `kill_session` refuses to kill the session containing the MCP pane
- `kill_window` refuses to kill the window containing the MCP pane
- `kill_pane` refuses to kill the pane running the MCP server

These protections use the `TMUX_PANE` environment variable to detect the caller's own pane.
