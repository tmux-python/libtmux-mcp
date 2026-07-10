```{eval-rst}
.. _safety:
```

# Safety tiers

libtmux-mcp uses a three-tier safety system to control which tools are available to AI agents.

## Overview

| Tier | Label | Access | Use case |
|------|-------|--------|----------|
| `readonly` | {badge}`readonly` | List, capture, search, info, readonly batches | Monitoring, browsing |
| `mutating` (default) | {badge}`mutating` | + create, {toolref}`send-keys`, {toolref}`send-keys-batch`, mutating batches, rename, resize | Normal agent workflow |
| `destructive` | {badge}`destructive` | + destructive batches, {toolref}`kill-server`, {toolref}`kill-session`, {toolref}`kill-window`, {toolref}`kill-pane` | Full control |

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

1. **[FastMCP](https://gofastmcp.com) tag visibility**: Tools are tagged with their tier. Only tags at or below the configured tier are enabled via `mcp.enable(tags=..., only=True)`.

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

The self-kill guard resolves the target server's socket path in three
steps ({func}`~libtmux_mcp._utils._effective_socket_path` in
`src/libtmux_mcp/_utils.py`):

1. Use {attr}`libtmux.Server.socket_path` if {external+libtmux:doc}`libtmux <index>` already has it.
2. Otherwise query the running server via `display-message -p '#{socket_path}'` — authoritative because tmux itself reports the path it is actually using, regardless of the MCP process environment. This closes the launchd-vs-interactive-shell gap on macOS where {envvar}`TMUX_TMPDIR` commonly differs between contexts.
3. Fall back to reconstruction from {envvar}`TMUX_TMPDIR` (or `/tmp`) + euid + socket name. Only reached when the target server is unreachable (not running), in which case no self-kill is possible anyway and {func}`~libtmux_mcp._utils._caller_is_on_server`'s None-socket branch blocks conservatively.

The structural fix shipped in 0.1.x; setting {envvar}`TMUX_TMPDIR` explicitly is no longer required for the guard to work, though it remains a useful diagnostic when investigating mismatched-path bug reports.

## Footguns inside the `mutating` tier

Most `mutating` tools are bounded: {toolref}`resize-pane` only
resizes, {toolref}`rename-window` only renames. A few have broader
reach because tmux itself exposes broader reach. Treat these as
elevated risk even though they share the default tier:

### Piping pane output

{tool}`pipe-pane` pipes a pane's output to a shell command that the server runs. In practice this means the caller chooses an arbitrary path or pipeline on the server host. There is no allow-list. Assume it can create files anywhere the server process can write.

Mitigations:

- Run the server as an unprivileged user with a scoped home directory.
- Consider `LIBTMUX_SAFETY=readonly` for untrusted MCP clients.
- Audit log records (see below) capture the `output_path` argument so reviewers can spot unexpected destinations.

### Setting tmux environment

{tool}`set-environment` writes into tmux's global, session, or window environment. Those values propagate into every shell tmux spawns afterwards. An agent that writes `PATH`, `LD_PRELOAD`, or `AWS_*` variables can influence every future command on that scope — including commands the user runs directly, not just commands the agent issues.

Mitigations:

- The server audit record replaces the `value` argument with a `{len, sha256_prefix}` digest, so the value does not appear verbatim in `libtmux_mcp.audit`. That redaction does not cover separate library, process, application, or client logs, so operators should still treat the tool as high-privilege.
- If only a single command needs a non-sensitive env override, prefer having the agent invoke `env VAR=value command` via {tooliconl}`send-keys` instead — the blast radius is one command, not every future child. For credentials, pass a reference that the child resolves instead of a literal value through tmux.

### Respawning panes

{tool}`respawn-pane` restarts a pane's process while preserving the pane id and layout — exactly what an agent wants when a shell wedges. Default `kill=True` terminates the running process before relaunch. The `pane_id` and layout are preserved (the point of the tool), but any unsaved REPL state, ssh session, or in-flight job in that pane is lost. Repeated calls are *not* idempotent — each call kills a new process.

Unlike other `mutating` tools, the registration carries `destructiveHint=True` and `idempotentHint=False` (via the `ANNOTATIONS_MUTATING_DESTRUCTIVE` preset) so MCP clients see honest annotations even though the tier tag stays at `mutating` for default-profile recovery.

Mitigations:

- `pane_id` is required (no fallback to "first pane in session/window"). Agents that pass only `session_name` get an {exc}`~libtmux_mcp._utils.ExpectedToolError` instead of an unintended kill — resolve via {tool}`list-panes` first.
- Any `shell` argument is briefly visible in the OS process table and tmux's `pane_current_command` metadata before the spawned shell takes over; the audit log redacts `shell` payloads (see below), but do not pass credentials directly even with redaction.
- The optional `environment` argument accepts either a mapping of string keys and values or a JSON object string, then maps each item to one tmux `-e KEY=VALUE` flag. For a mapping, the audit log keeps each *key* visible and replaces each *value* with a `{len, sha256_prefix}` digest. A JSON string is redacted as one scalar digest, so its keys are not retained in the audit record. The same OS-process-table caveat as `shell` applies: `respawn-pane -e DB_PASSWORD=...` may briefly appear in `ps` output before the spawned process inherits the env.
- The same self-pane guard that protects the destructive kill commands also refuses to respawn the pane running the MCP server.

### Raw pane input

These can execute anything the pane's shell accepts. There is no payload validation. The server audit log stores a digest of the content, not the content itself, so a secret typed via {tooliconl}`send-keys` or {tooliconl}`send-keys-batch` does not land in that audit record.

### History suppression is not secret transport

`suppress_history` on {tooliconl}`run-command` asks the current shell not to persist one space-prefixed command event. `suppress_persistent_history=true` on the four spawn tools adds best-effort no-disk controls to a new environment. Shell behavior and startup files can defeat either request. History suppression does not isolate the process, does not clear in-memory history or scrollback, and does not hide the command from other observation surfaces:

- **pane echo and scrollback:** the terminal can display input, tmux can retain it in pane history, and an attached terminal can keep its own scrollback.
- **capture tools and piping:** {tooliconl}`capture-pane`, {tooliconl}`capture-since`, {tooliconl}`snapshot-pane`, {tooliconl}`search-panes`, and {tooliconl}`pipe-pane` can return or route displayed and retained text.
- **hooks:** configured tmux hooks, including state visible through {tooliconl}`show-hooks`, and shell instrumentation can observe process or pane activity independently of shell history.
- **process visibility:** command arguments and launch strings can appear in the tmux client argv. Environment values passed to {toolref}`create-session`, {toolref}`create-window`, {toolref}`split-window`, and {toolref}`respawn-pane` can also remain in a child process environment; {toolref}`create-session` retains them in tmux session state for future panes. MCP audit redaction does not hide any of these surfaces from host process or tmux environment inspection.
- **MCP client transcripts:** clients can retain the original request and response outside the server's control.
- **logs:** `libtmux_mcp.audit` records redacted arguments and whether the call succeeded or raised; it does not contain tool return values. Redaction applies only to these audit records and does not rewrite separate records emitted by libtmux, FastMCP, shells, or MCP clients. libtmux DEBUG or error records may contain shell-joined tmux arguments, while MCP client request logs and application logs remain outside the server's guarantee.

Prefer credential references that a process resolves from a secret manager, scoped file descriptor, or preconfigured host lookup. Avoid literal credentials in `command`, raw `keys` or `text`, `shell`, and `environment` arguments; history suppression cannot retract a value after another surface records it.

## Audit log

Every tool call emits one `INFO` record on the `libtmux_mcp.audit` logger carrying:

- `tool` — the tool name
- `outcome` — `ok` or `error`, with `error_type` on failure
- `duration_ms`
- `client_id` / `request_id` — from the fastmcp context when available
- `args` — a summary of arguments. Sensitive scalar keys (`keys`, `text`, `command`, `value`, `content`, `shell`, and string-form `environment`) are replaced by `{len, sha256_prefix}`. Mapping-form `environment` keeps its keys but digests each value individually. Non-sensitive strings over 200 characters are truncated.

Route this logger to a dedicated sink if you want a durable audit trail; it is deliberately namespaced separately from the main `libtmux_mcp` logger.

## Tool annotations

Each tool carries MCP tool annotations that hint at its behavior:

| Tool | Tier | readOnly | destructive | idempotent |
|------|------|----------|-------------|------------|
| {toolref}`list-sessions` | {badge}`readonly` | true | false | true |
| {toolref}`get-server-info` | {badge}`readonly` | true | false | true |
| {toolref}`list-windows` | {badge}`readonly` | true | false | true |
| {toolref}`list-panes` | {badge}`readonly` | true | false | true |
| {toolref}`capture-pane` | {badge}`readonly` | true | false | true |
| {toolref}`capture-since` | {badge}`readonly` | true | false | true |
| {toolref}`get-pane-info` | {badge}`readonly` | true | false | true |
| {toolref}`search-panes` | {badge}`readonly` | true | false | true |
| {toolref}`wait-for-text` | {badge}`readonly` | true | false | true |
| {toolref}`show-option` | {badge}`readonly` | true | false | true |
| {toolref}`show-environment` | {badge}`readonly` | true | false | true |
| {toolref}`create-session` | {badge}`mutating` | false | false | false |
| {toolref}`create-window` | {badge}`mutating` | false | false | false |
| {toolref}`split-window` | {badge}`mutating` | false | false | false |
| {toolref}`send-keys` | {badge}`mutating` | false | false | false |
| {toolref}`rename-session` | {badge}`mutating` | false | false | true |
| {toolref}`rename-window` | {badge}`mutating` | false | false | true |
| {toolref}`resize-pane` | {badge}`mutating` | false | false | true |
| {toolref}`resize-window` | {badge}`mutating` | false | false | true |
| {toolref}`set-pane-title` | {badge}`mutating` | false | false | true |
| {toolref}`clear-pane` | {badge}`mutating` | false | true | false |
| {toolref}`select-layout` | {badge}`mutating` | false | false | true |
| {toolref}`set-option` | {badge}`mutating` | false | false | true |
| {toolref}`set-environment` | {badge}`mutating` | false | false | true |
| {toolref}`respawn-pane` | {badge}`mutating` | false | true | false |
| {toolref}`kill-server` | {badge}`destructive` | false | true | false |
| {toolref}`kill-session` | {badge}`destructive` | false | true | false |
| {toolref}`kill-window` | {badge}`destructive` | false | true | false |
| {toolref}`kill-pane` | {badge}`destructive` | false | true | false |
