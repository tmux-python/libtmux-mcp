(safety)=

# Safety tiers

libtmux-mcp uses a three-tier safety system to control which tools are available to AI agents.

## Overview

| Tier | Label | Access | Use case |
|------|-------|--------|----------|
| `readonly` | {badge}`readonly` | List, capture, search, info | Monitoring, browsing |
| `mutating` (default) | {badge}`mutating` | + create, send_keys, rename, resize | Normal agent workflow |
| `destructive` | {badge}`destructive` | + kill_server, kill_session, kill_window, kill_pane | Full control |

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

- {badge}`readonly` `readonly` — Read-only operations that don't modify tmux state
- {badge}`mutating` `mutating` — Operations that create, modify, or send input to tmux objects
- {badge}`destructive` `destructive` — Operations that destroy tmux objects (kill commands)

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

| Tool | Tier | Label | readOnly | destructive | idempotent |
|------|------|-------|----------|-------------|------------|
| `list_sessions` | readonly | {badge}`readonly` | true | false | true |
| `get_server_info` | readonly | {badge}`readonly` | true | false | true |
| `list_windows` | readonly | {badge}`readonly` | true | false | true |
| `list_panes` | readonly | {badge}`readonly` | true | false | true |
| `capture_pane` | readonly | {badge}`readonly` | true | false | true |
| `get_pane_info` | readonly | {badge}`readonly` | true | false | true |
| `search_panes` | readonly | {badge}`readonly` | true | false | true |
| `wait_for_text` | readonly | {badge}`readonly` | true | false | true |
| `show_option` | readonly | {badge}`readonly` | true | false | true |
| `show_environment` | readonly | {badge}`readonly` | true | false | true |
| `create_session` | mutating | {badge}`mutating` | false | false | false |
| `create_window` | mutating | {badge}`mutating` | false | false | false |
| `split_window` | mutating | {badge}`mutating` | false | false | false |
| `send_keys` | mutating | {badge}`mutating` | false | false | false |
| `rename_session` | mutating | {badge}`mutating` | false | false | true |
| `rename_window` | mutating | {badge}`mutating` | false | false | true |
| `resize_pane` | mutating | {badge}`mutating` | false | false | true |
| `resize_window` | mutating | {badge}`mutating` | false | false | true |
| `set_pane_title` | mutating | {badge}`mutating` | false | false | true |
| `clear_pane` | mutating | {badge}`mutating` | false | false | true |
| `select_layout` | mutating | {badge}`mutating` | false | false | true |
| `set_option` | mutating | {badge}`mutating` | false | false | true |
| `set_environment` | mutating | {badge}`mutating` | false | false | true |
| `kill_server` | destructive | {badge}`destructive` | false | true | false |
| `kill_session` | destructive | {badge}`destructive` | false | true | false |
| `kill_window` | destructive | {badge}`destructive` | false | true | false |
| `kill_pane` | destructive | {badge}`destructive` | false | true | false |
