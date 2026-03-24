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

## Tool annotations

Each tool carries MCP tool annotations that hint at its behavior:

| Tool | Tier | readOnly | destructive | idempotent |
|------|------|----------|-------------|------------|
| `list_sessions` | readonly | true | false | true |
| `get_server_info` | readonly | true | false | true |
| `list_windows` | readonly | true | false | true |
| `list_panes` | readonly | true | false | true |
| `capture_pane` | readonly | true | false | true |
| `get_pane_info` | readonly | true | false | true |
| `search_panes` | readonly | true | false | true |
| `wait_for_text` | readonly | true | false | true |
| `show_option` | readonly | true | false | true |
| `show_environment` | readonly | true | false | true |
| `create_session` | mutating | false | false | false |
| `create_window` | mutating | false | false | false |
| `split_window` | mutating | false | false | false |
| `send_keys` | mutating | false | false | false |
| `rename_session` | mutating | false | false | true |
| `rename_window` | mutating | false | false | true |
| `resize_pane` | mutating | false | false | true |
| `resize_window` | mutating | false | false | true |
| `set_pane_title` | mutating | false | false | true |
| `clear_pane` | mutating | false | false | true |
| `select_layout` | mutating | false | false | true |
| `set_option` | mutating | false | false | true |
| `set_environment` | mutating | false | false | true |
| `kill_server` | destructive | false | true | false |
| `kill_session` | destructive | false | true | false |
| `kill_window` | destructive | false | true | false |
| `kill_pane` | destructive | false | true | false |
