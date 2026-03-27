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

Set the safety tier via the {ref}`LIBTMUX_SAFETY` environment variable:

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

- {tool}`kill-server` refuses to run if the MCP server is inside the target server
- {tool}`kill-session` refuses to kill the session containing the MCP pane
- {tool}`kill-window` refuses to kill the window containing the MCP pane
- {tool}`kill-pane` refuses to kill the pane running the MCP server

These protections use the `TMUX_PANE` environment variable to detect the caller's own pane.

## Tool annotations

Each tool carries MCP tool annotations that hint at its behavior:

| Tool | Tier | readOnly | destructive | idempotent |
|------|------|----------|-------------|------------|
| {ref}`list-sessions` | {badge}`readonly` | true | false | true |
| {ref}`get-server-info` | {badge}`readonly` | true | false | true |
| {ref}`list-windows` | {badge}`readonly` | true | false | true |
| {ref}`list-panes` | {badge}`readonly` | true | false | true |
| {ref}`capture-pane` | {badge}`readonly` | true | false | true |
| {ref}`get-pane-info` | {badge}`readonly` | true | false | true |
| {ref}`search-panes` | {badge}`readonly` | true | false | true |
| {ref}`wait-for-text` | {badge}`readonly` | true | false | true |
| {ref}`show-option` | {badge}`readonly` | true | false | true |
| {ref}`show-environment` | {badge}`readonly` | true | false | true |
| {ref}`create-session` | {badge}`mutating` | false | false | false |
| {ref}`create-window` | {badge}`mutating` | false | false | false |
| {ref}`split-window` | {badge}`mutating` | false | false | false |
| {ref}`send-keys` | {badge}`mutating` | false | false | false |
| {ref}`rename-session` | {badge}`mutating` | false | false | true |
| {ref}`rename-window` | {badge}`mutating` | false | false | true |
| {ref}`resize-pane` | {badge}`mutating` | false | false | true |
| {ref}`resize-window` | {badge}`mutating` | false | false | true |
| {ref}`set-pane-title` | {badge}`mutating` | false | false | true |
| {ref}`clear-pane` | {badge}`mutating` | false | false | true |
| {ref}`select-layout` | {badge}`mutating` | false | false | true |
| {ref}`set-option` | {badge}`mutating` | false | false | true |
| {ref}`set-environment` | {badge}`mutating` | false | false | true |
| {ref}`kill-server` | {badge}`destructive` | false | true | false |
| {ref}`kill-session` | {badge}`destructive` | false | true | false |
| {ref}`kill-window` | {badge}`destructive` | false | true | false |
| {ref}`kill-pane` | {badge}`destructive` | false | true | false |
