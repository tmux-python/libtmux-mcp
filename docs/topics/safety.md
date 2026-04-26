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

Set the safety tier via the {envvar}`LIBTMUX_SAFETY` environment variable:

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

These protections read both the `TMUX` and `TMUX_PANE` environment variables that tmux injects into pane child processes. The `TMUX` value is formatted `socket_path,server_pid,session_id` — libtmux-mcp parses the socket path and compares it to the target server's so the guard only fires when the caller is actually on the same tmux server. A kill across unrelated sockets is allowed; a kill of the caller's own pane/window/session/server is refused. If the caller's socket can't be determined (rare — `TMUX_PANE` set without `TMUX`), the guard errs on the side of blocking.

### macOS `TMUX_TMPDIR` caveat

The self-kill guard resolves the target server's socket path in three steps (`_effective_socket_path` in `src/libtmux_mcp/_utils.py`):

1. Use `Server.socket_path` if libtmux already has it.
2. Otherwise query the running server via `display-message -p '#{socket_path}'` — authoritative because tmux itself reports the path it is actually using, regardless of the MCP process environment. This closes the launchd-vs-interactive-shell gap on macOS where {envvar}`TMUX_TMPDIR` commonly differs between contexts.
3. Fall back to reconstruction from {envvar}`TMUX_TMPDIR` (or `/tmp`) + euid + socket name. Only reached when the target server is unreachable (not running), in which case no self-kill is possible anyway and `_caller_is_on_server`'s None-socket branch blocks conservatively.

The structural fix shipped in 0.1.x; setting {envvar}`TMUX_TMPDIR` explicitly is no longer required for the guard to work, though it remains a useful diagnostic when investigating mismatched-path bug reports.

## Footguns inside the `mutating` tier

Most `mutating` tools are bounded: `resize_pane` only resizes, `rename_window` only renames. A few have broader reach because tmux itself exposes broader reach. Treat these as elevated risk even though they share the default tier:

### `pipe_pane`

{tool}`pipe-pane` pipes a pane's output to a shell command that the server runs. In practice this means the caller chooses an arbitrary path or pipeline on the server host. There is no allow-list. Assume it can create files anywhere the server process can write.

Mitigations:

- Run the server as an unprivileged user with a scoped home directory.
- Consider `LIBTMUX_SAFETY=readonly` for untrusted MCP clients.
- Audit log records (see below) capture the `output_path` argument so reviewers can spot unexpected destinations.

### `set_environment`

{tool}`set-environment` writes into tmux's global, session, or window environment. Those values propagate into every shell tmux spawns afterwards. An agent that writes `PATH`, `LD_PRELOAD`, or `AWS_*` variables can influence every future command on that scope — including commands the user runs directly, not just commands the agent issues.

Mitigations:

- The audit log redacts the `value` argument to a `{len, sha256_prefix}` digest so log files don't leak the secrets agents set, but operators should still treat the tool as high-privilege.
- If only a single command needs an env override, prefer having the agent invoke `env VAR=value command` via `send_keys` instead — the blast radius is one command, not every future child.

### `respawn_pane`

{tool}`respawn-pane` restarts a pane's process while preserving the pane id and layout — exactly what an agent wants when a shell wedges. Default `kill=True` terminates the running process before relaunch. The `pane_id` and layout are preserved (the point of the tool), but any unsaved REPL state, ssh session, or in-flight job in that pane is lost. Repeated calls are *not* idempotent — each call kills a new process.

Unlike other `mutating` tools, the registration carries `destructiveHint=True` and `idempotentHint=False` (via the `ANNOTATIONS_MUTATING_DESTRUCTIVE` preset) so MCP clients see honest annotations even though the tier tag stays at `mutating` for default-profile recovery.

Mitigations:

- `pane_id` is required (no fallback to "first pane in session/window"). Agents that pass only `session_name` get a `ToolError` instead of an unintended kill — resolve via {tool}`list-panes` first.
- Any `shell` argument is briefly visible in the OS process table and tmux's `pane_current_command` metadata before the spawned shell takes over; the audit log redacts `shell` payloads (see below), but do not pass credentials directly even with redaction.
- The optional `environment` argument (`dict[str, str]`) maps to one tmux `-e KEY=VALUE` flag per item. The audit log redacts each *value* via a `{len, sha256_prefix}` digest while keeping the *keys* visible — env var names like `DATABASE_URL` are usually operator-debug-useful, but their values are the secret. The same OS-process-table caveat as `shell` applies: `respawn-pane -e DB_PASSWORD=...` may briefly appear in `ps` output before the spawned process inherits the env.
- The same self-pane guard that protects the destructive kill commands also refuses to respawn the pane running the MCP server.

### `send_keys` / `paste_text`

These can execute anything the pane's shell accepts. There is no payload validation. The audit log stores a digest of the content, not the content itself, so a secret typed via `send_keys` does not land in logs.

## Audit log

Every tool call emits one `INFO` record on the `libtmux_mcp.audit` logger carrying:

- `tool` — the tool name
- `outcome` — `ok` or `error`, with `error_type` on failure
- `duration_ms`
- `client_id` / `request_id` — from the fastmcp context when available
- `args` — a summary of arguments. Sensitive scalar keys (`keys`, `text`, `value`, `content`, `shell`) are replaced by `{len, sha256_prefix}`; the dict-shaped sensitive key `environment` keeps its keys but digests each value individually. Non-sensitive strings over 200 characters are truncated.

Route this logger to a dedicated sink if you want a durable audit trail; it is deliberately namespaced separately from the main `libtmux_mcp` logger.

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
| {ref}`respawn-pane` | {badge}`mutating` | false | true | false |
| {ref}`kill-server` | {badge}`destructive` | false | true | false |
| {ref}`kill-session` | {badge}`destructive` | false | true | false |
| {ref}`kill-window` | {badge}`destructive` | false | true | false |
| {ref}`kill-pane` | {badge}`destructive` | false | true | false |
