```{eval-rst}
.. _trust:
```

# Trust model

This server gives an agent a terminal. What follows is what that does and
does not bound.

## Toolsets are an inventory, not a permission system

Tools are grouped into four sets by what they do:

`inspect`
: Read tmux state and terminal output. Starts no process, and hands no
  caller input to one — what you supply is IDs, names, bounded patterns,
  and validated variable names.

`manage`
: Change tmux structure or presentation: names, sizes, layouts, selections,
  modes. Starts no process, and takes no caller input that anything later
  executes.

`execute`
: Start a pane process, deliver input to one, or store a value tmux later
  runs. {tooliconl}`set-option` is here, not in `manage`: a `#(...)` job in
  a status format runs when tmux draws it and repeats on the status
  interval, and `default-command` decides what every future pane runs.

`teardown`
: Delete tmux objects or retained scrollback. Irreversible at the tmux
  level.

The sets are unordered. `LIBTMUX_TOOLSETS=inspect,teardown` is a legal
surface — an agent that can look and clean up, but not type.

**Dropping a toolset is not containment.** It changes what this server
advertises. An enabled `execute` tool can type the equivalent of anything
you hid, because a pane's shell runs with your user's authority. Treat the
toolsets as inventory configuration and accident reduction. OS accounts,
containers, and separate tmux sockets are the isolation boundaries.

## `inspect` does not mean safe

An `inspect` tool does not interpret what you give it as a command. That is
a property of these implementations, and it is the only thing the name
claims.

It is not a claim that the result is harmless. A capture returns whatever
the pane holds: credentials someone typed, a command line with a token in
it, output from a remote host, text written by another agent. Those reads
advertise `openWorldHint: true` for that reason. Auto-approving the whole
set is a decision to make with that in mind, not one the name endorses.

## Configuration

```json
{
    "mcpServers": {
        "libtmux": {
            "command": "uvx",
            "args": ["libtmux-mcp"],
            "env": {
                "LIBTMUX_TOOLSETS": "inspect"
            }
        }
    }
}
```

{envvar}`LIBTMUX_TOOLSETS` is a comma list, defaulting to
`inspect,manage,execute`. `teardown` is not in the default: this server
reaches whichever tmux server the environment points at, so deletion is
something you ask for by name. {envvar}`LIBTMUX_TOOLS` enables individual
tools regardless of toolset, and {envvar}`LIBTMUX_EXCLUDE_TOOLS` refuses
them regardless of every enable above.

An unknown toolset or tool name fails startup rather than being ignored.
A typo that silently widened a surface you believed was narrow is worse
than a server that will not start.

### How it works

Two layers, both keyed on the same tags. [FastMCP](https://gofastmcp.com)
tag visibility filters the listing; a middleware repeats the decision on
call so a direct invocation gets an error naming the variable rather than
an unknown-tool error.

Both fail closed: a tool carrying no recognized toolset is refused, so
adding one without classifying it cannot expose it by accident.

## Self-kill protection

The `teardown` tools include safeguards against self-harm:

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

## Footguns inside `execute`

Most `manage` tools are bounded: {toolref}`resize-pane` only
resizes, {toolref}`rename-window` only renames. A few have broader
reach because tmux itself exposes broader reach. Treat these as
elevated risk even though they share the default tier:

### Piping pane output

{tool}`pipe-pane` pipes a pane's output to a shell command that the server runs. In practice this means the caller chooses an arbitrary path or pipeline on the server host. There is no allow-list. Assume it can create files anywhere the server process can write.

Mitigations:

- Run the server as an unprivileged user with a scoped home directory.
- Consider `LIBTMUX_TOOLSETS=inspect` for untrusted MCP clients.
- Audit log records (see below) capture the `output_path` argument so reviewers can spot unexpected destinations.

### Setting tmux environment

{tool}`set-environment` writes into tmux's global, session, or window environment. Those values propagate into every shell tmux spawns afterwards. An agent that writes `PATH`, `LD_PRELOAD`, or `AWS_*` variables can influence every future command on that scope — including commands the user runs directly, not just commands the agent issues.

Mitigations:

- The server audit record replaces the `value` argument with a `{len, sha256_prefix}` digest, so the value does not appear verbatim in `libtmux_mcp.audit`. That redaction does not cover separate library, process, application, or client logs, so operators should still treat the tool as high-privilege.
- If only a single command needs a non-sensitive env override, prefer having the agent invoke `env VAR=value command` via {tooliconl}`send-keys` instead — the blast radius is one command, not every future child. For credentials, pass a reference that the child resolves instead of a literal value through tmux.

### Respawning panes

{tool}`respawn-pane` restarts a pane's process while preserving the pane id and layout — exactly what an agent wants when a shell wedges. Default `kill=True` terminates the running process before relaunch. The `pane_id` and layout are preserved (the point of the tool), but any unsaved REPL state, ssh session, or in-flight job in that pane is lost. Repeated calls are *not* idempotent — each call kills a new process.

The registration advertises `destructiveHint=True` and `idempotentHint=False` while staying in `manage`, so recovery remains available by default without understating what the call does.

Mitigations:

- `pane_id` is required (no fallback to "first pane in session/window"). Agents that pass only `session_name` get an {exc}`~libtmux_mcp._utils.ExpectedToolError` instead of an unintended kill — resolve via {tool}`list-panes` first.
- Any `shell` argument is briefly visible in the OS process table and tmux's `pane_current_command` metadata before the spawned shell takes over; the audit log redacts `shell` payloads (see below), but do not pass credentials directly even with redaction.
- The optional `environment` argument accepts either a mapping of string keys and values or a JSON object string, then maps each item to one tmux `-e KEY=VALUE` flag. For a mapping, the audit log keeps each *key* visible and replaces each *value* with a `{len, sha256_prefix}` digest. A JSON string is redacted as one scalar digest, so its keys are not retained in the audit record. The same OS-process-table caveat as `shell` applies: `respawn-pane -e DB_PASSWORD=...` may briefly appear in `ps` output before the spawned process inherits the env.
- The same self-pane guard that protects the kill commands also refuses to respawn the pane running the MCP server.

### Raw pane input

These can execute anything the pane's shell accepts. There is no payload validation. The server audit log stores a digest of the content, not the content itself, so a secret typed via {tooliconl}`send-keys` or {tooliconl}`send-keys-batch` does not land in that audit record.

### History suppression is not secret transport

`suppress_history` on {tooliconl}`run-command` asks the current shell not to persist one space-prefixed command event. `suppress_persistent_history=true` on the four spawn tools adds best-effort no-disk controls to a new environment. Shell behavior and startup files can defeat either request. History suppression does not isolate the process, does not clear in-memory history or scrollback, and does not hide the command from other observation surfaces:

- **pane echo and scrollback:** the terminal can display input, tmux can retain it in pane history, and an attached terminal can keep its own scrollback.
- **capture tools and piping:** {toolref}`capture-pane`, {toolref}`capture-since`, {toolref}`snapshot-pane`, {toolref}`search-panes`, and {toolref}`pipe-pane` can return or route displayed and retained text.
- **hooks:** configured tmux hooks, including state visible through {tooliconl}`show-hooks`, and shell instrumentation can observe process or pane activity independently of shell history.
- **process visibility:** command arguments and launch strings can appear in the tmux client argv. Environment values passed to {toolref}`create-session`, {toolref}`create-window`, {toolref}`split-window`, and {toolref}`respawn-pane` can also remain in a child process environment; {toolref}`create-session` retains them in tmux session state for future panes, where {toolref}`show-environment` can reveal them. MCP audit redaction does not hide any of these surfaces from host process or tmux environment inspection.
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

Every tool advertises the four MCP annotation hints. They are hints for client
presentation, not authorization: a client may ignore them, and this server
cannot enforce them.

`destructiveHint: false` is a claim that a tool performs **only additive
updates**, so a tool that replaces a name, a size, or a layout advertises
`true` even though nothing is destroyed. `openWorldHint: true` says the tool
reaches, or returns text from, outside tmux — a spawned process runs with your
user's authority, and a pane holds whatever was printed into it.

| Tool | Toolset | readOnlyHint | destructiveHint | idempotentHint | openWorldHint |
|------|---------|--------------|-----------------|----------------|---------------|
| {toolref}`call-read-tools-batch` | {badge}`inspect` | true | false | true | true |
| {toolref}`capture-pane` | {badge}`inspect` | true | false | true | true |
| {toolref}`capture-since` | {badge}`inspect` | true | false | true | true |
| {toolref}`display-message` | {badge}`inspect` | true | false | true | true |
| {toolref}`find-pane-by-position` | {badge}`inspect` | true | false | true | false |
| {toolref}`get-pane-info` | {badge}`inspect` | true | false | true | false |
| {toolref}`get-server-info` | {badge}`inspect` | true | false | true | false |
| {toolref}`get-session-info` | {badge}`inspect` | true | false | true | false |
| {toolref}`get-window-info` | {badge}`inspect` | true | false | true | false |
| {toolref}`list-panes` | {badge}`inspect` | true | false | true | false |
| {toolref}`list-servers` | {badge}`inspect` | true | false | true | false |
| {toolref}`list-sessions` | {badge}`inspect` | true | false | true | false |
| {toolref}`list-windows` | {badge}`inspect` | true | false | true | false |
| {toolref}`search-panes` | {badge}`inspect` | true | false | true | true |
| {toolref}`show-buffer` | {badge}`inspect` | true | false | true | true |
| {toolref}`show-environment` | {badge}`inspect` | true | false | true | false |
| {toolref}`show-hook` | {badge}`inspect` | true | false | true | false |
| {toolref}`show-hooks` | {badge}`inspect` | true | false | true | false |
| {toolref}`show-option` | {badge}`inspect` | true | false | true | false |
| {toolref}`snapshot-pane` | {badge}`inspect` | true | false | true | true |
| {toolref}`wait-for-text` | {badge}`inspect` | true | false | true | true |
| {toolref}`enter-copy-mode` | {badge}`manage` | false | true | false | false |
| {toolref}`exit-copy-mode` | {badge}`manage` | false | true | true | false |
| {toolref}`load-buffer` | {badge}`manage` | false | false | false | false |
| {toolref}`move-window` | {badge}`manage` | false | true | true | false |
| {toolref}`rename-session` | {badge}`manage` | false | true | true | false |
| {toolref}`rename-window` | {badge}`manage` | false | true | true | false |
| {toolref}`resize-pane` | {badge}`manage` | false | true | true | false |
| {toolref}`resize-window` | {badge}`manage` | false | true | true | false |
| {toolref}`select-layout` | {badge}`manage` | false | true | true | false |
| {toolref}`select-pane` | {badge}`manage` | false | true | true | false |
| {toolref}`select-window` | {badge}`manage` | false | true | true | false |
| {toolref}`set-pane-title` | {badge}`manage` | false | true | true | false |
| {toolref}`signal-channel` | {badge}`manage` | false | true | false | false |
| {toolref}`swap-pane` | {badge}`manage` | false | true | false | false |
| {toolref}`wait-for-channel` | {badge}`manage` | false | true | false | false |
| {toolref}`create-session` | {badge}`execute` | false | false | false | true |
| {toolref}`create-window` | {badge}`execute` | false | false | false | true |
| {toolref}`paste-buffer` | {badge}`execute` | false | true | false | true |
| {toolref}`paste-text` | {badge}`execute` | false | true | false | true |
| {toolref}`pipe-pane` | {badge}`execute` | false | true | false | true |
| {toolref}`respawn-pane` | {badge}`execute` | false | true | false | true |
| {toolref}`run-command` | {badge}`execute` | false | true | false | true |
| {toolref}`send-keys` | {badge}`execute` | false | true | false | true |
| {toolref}`send-keys-batch` | {badge}`execute` | false | true | false | true |
| {toolref}`set-environment` | {badge}`execute` | false | true | true | true |
| {toolref}`set-option` | {badge}`execute` | false | true | true | true |
| {toolref}`split-window` | {badge}`execute` | false | true | false | true |
| {toolref}`clear-pane` | {badge}`teardown` | false | true | false | false |
| {toolref}`delete-buffer` | {badge}`teardown` | false | true | false | false |
| {toolref}`kill-pane` | {badge}`teardown` | false | true | false | false |
| {toolref}`kill-server` | {badge}`teardown` | false | true | false | false |
| {toolref}`kill-session` | {badge}`teardown` | false | true | false | false |
| {toolref}`kill-window` | {badge}`teardown` | false | true | false | false |
