```{eval-rst}
.. _trust:
```

# Trust model

This server gives an agent a terminal. What follows is what that does and
does not bound.

## Toolsets gate MCP tool calls, not tmux

Tools are grouped into four sets by what they do:

{badge}`inspect`: Request tmux state or terminal output, or render
server-local prompt text. The built-in operation does not pass caller input as
a tmux or shell command.

{badge}`manage`: Change tmux-managed structure, presentation, staging, or
coordination state. The built-in operation does not supply a shell command,
pane input, or a value tmux treats as executable configuration.

{badge}`execute`: Start a pane process, deliver input to one, or store state
that can control later execution. {tooliconl}`set-option` is here, not in
`manage`: a `#(...)` job in a status format runs when tmux draws it and repeats
on the status interval, and `default-command` decides what every future pane
runs.

{badge}`teardown`: Delete tmux objects or retained scrollback. Irreversible
at the tmux level.

The sets are unordered. FastMCP visibility and libtmux-mcp middleware both
enforce which MCP tool calls the server advertises and accepts.
`LIBTMUX_TOOLSETS=inspect,teardown` is therefore a legal surface — an agent
that can look and clean up through this server's tools, but not type through
them.

{envvar}`LIBTMUX_TOOLSETS`, {envvar}`LIBTMUX_TOOLS`, and
{envvar}`LIBTMUX_EXCLUDE_TOOLS` filter tools only. The `tmux://` hierarchy
resources and native prompts remain available when every toolset is disabled.

**Dropping a toolset is not containment.** It changes what this server
advertises. An enabled `execute` tool can type the equivalent of anything
you hid, because a pane's shell runs with your user's authority. Existing pane
processes and other clients of the same tmux server also remain outside the MCP
call gate.

## The tmux server is programmable

tmux is a separate, long-lived process. A configured `command-alias` can
replace a command this server sends, and an `after-*` hook can run a command
list after many built-in commands. A nominal `inspect` call can therefore
change state or run a shell without receiving executable input from the MCP
caller.

Hierarchy resource reads are a separate MCP surface, but they send the same
class of tmux queries as `inspect` tools. A `resources/read` request can
therefore activate aliases and hooks too. Resources have no ToolAnnotations
and do not produce this server's tool-call audit record. Native prompts only
return text and do not contact tmux.

Execution can occur without any MCP call. A `#(...)` job in a status format
runs when tmux redraws the status line and can repeat on the status interval.
No MCP tool filter can intercept work that never passes through this server.

libtmux-mcp startup and shutdown send no tmux commands. Failed tool calls run
once: the server does not retry them automatically because tmux may already
have applied an alias or hook effect before reporting an error.

A toolset describes the built-in operation its tools request. It does not
describe everything the target tmux server may do around that request.

### Responsibility by layer

| Layer | Owns |
| --- | --- |
| libtmux-mcp | Input validation and refusal, tmux argv construction, the advertised and callable tool surface, direct-operation classification, wait ceilings, selected high-volume output caps, resource disclosure, and tool-call audit redaction. |
| Model or agent | Chooses requested calls and command text, but is not an enforcement boundary against its own errors or prompt injection. |
| MCP client and user | Whether to request and confirm a call, whether to retry it, and which credentials the agent receives. |
| tmux operator | The target socket, configuration, aliases, hooks, key bindings, status formats, pane programs, and other clients. |
| OS and deployment | Process identity and limits on filesystem, network, credentials, privileges, and resources. |

For local stdio use, the launching client and OS account are the trust context;
FastMCP has no OAuth token to authorize. A remote HTTP deployment must
authenticate users and enforce authorization on the server as well as asking
for client-side confirmation. See [FastMCP authorization](https://gofastmcp.com/servers/authorization).

### Guarantee by topology

| Topology | Strongest guarantee |
| --- | --- |
| Existing or shared tmux server | The MCP tool-call gate and libtmux-mcp's input handling; tmux configuration and peer activity remain unknown and mutable. |
| Fresh, separately supervised tmux server with a minimal config | A separate tmux object namespace and known startup configuration for that daemon generation; same-user clients and pane processes can still reconfigure it. |
| OS identity, container, or VM boundary | Effects are limited by the configured process, filesystem, network, credential, privilege, and resource policy. tmux still executes processes inside that boundary. |

A socket alone is an endpoint, not process confinement. Starting a normal tmux
client with `-f` also does not prove that configuration was used: if the server
already exists, tmux keeps the configuration from that daemon's startup.

## `inspect` does not mean safe

An `inspect` tool's built-in command sequence does not pass caller input as a
tmux or shell command. That is a property of these implementations, and it is
the only thing the name claims. A target tmux server may still replace or
extend the requested command through its aliases and hooks.

It is not a claim that the result is harmless. A capture returns whatever the
pane holds: credentials someone typed, a command line with a token in it,
output from a remote host, text written by another agent. Auto-approving the
whole set is a decision to make with that in mind, not one the name endorses.
Treat pane and hierarchy-resource output as untrusted data, never as
instructions.

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

Two layers use the same tags and names. [FastMCP](https://gofastmcp.com)
visibility is the primary wire filter: omitted or excluded tools disappear
from listings, and direct calls return an unknown-tool error. Middleware
rechecks the classification for tools that reach dispatch.

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
elevated risk even though the default enables their toolset:

### Piping pane output

{tool}`pipe-pane` pipes a pane's output through a fixed shell redirection. The
caller chooses the destination path. There is no path allow-list; assume it can
create files anywhere the server process can write.

Mitigations:

- Run the server as an unprivileged user with a scoped home directory.
- Exclude `execute` when pane control is unnecessary. This narrows the direct
  tool surface; it does not establish trust or confinement.
- Audit log records (see below) capture the `output_path` argument so reviewers can spot unexpected destinations.

### Setting tmux environment

{tool}`set-environment` writes into tmux's global, session, or window environment. Those values propagate into every shell tmux spawns afterwards. An agent that writes `PATH`, `LD_PRELOAD`, or `AWS_*` variables can influence every future command on that scope — including commands the user runs directly, not just commands the agent issues.

Mitigations:

- The server audit record replaces the `value` argument with a `{len, sha256_prefix}` digest, so the value does not appear verbatim in `libtmux_mcp.audit`. That redaction does not cover separate library, process, application, or client logs, so operators should still treat the tool as high-privilege.
- If only a single command needs a non-sensitive env override, prefer having the agent invoke `env VAR=value command` via {tooliconl}`send-keys` instead — the blast radius is one command, not every future child. For credentials, pass a reference that the child resolves instead of a literal value through tmux.

### Respawning panes

{tool}`respawn-pane` restarts a pane's process while preserving the pane id and layout — exactly what an agent wants when a shell wedges. Default `kill=True` terminates the running process before relaunch. The `pane_id` and layout are preserved (the point of the tool), but any unsaved REPL state, ssh session, or in-flight job in that pane is lost. Repeated calls are *not* idempotent — each call kills a new process.

The tool belongs to `execute`: it terminates one pane process and starts
another, even when the replacement command is omitted.

Mitigations:

- `pane_id` is required (no fallback to "first pane in session/window"). Agents that pass only `session_name` get an {exc}`~libtmux_mcp._utils.ExpectedToolError` instead of an unintended kill — resolve via {tool}`list-panes` first.
- Any `shell` argument is briefly visible in the OS process table and tmux's `pane_current_command` metadata before the spawned shell takes over; the audit log redacts `shell` payloads (see below), but do not pass credentials directly even with redaction.
- The optional `environment` argument accepts either a mapping of string keys and values or a JSON object string, then maps each item to one tmux `-e KEY=VALUE` flag. For a mapping, the audit log keeps each *key* visible and replaces each *value* with a `{len, sha256_prefix}` digest. A JSON string is redacted as one scalar digest, so its keys are not retained in the audit record. The same OS-process-table caveat as `shell` applies: `respawn-pane -e DB_PASSWORD=...` may briefly appear in `ps` output before the spawned process inherits the env.
- The same self-pane guard that protects the kill commands also refuses to respawn the pane running the MCP server.

### Raw pane input

These can execute anything the pane's shell accepts. There is no shell-syntax
allow-list. The server audit log stores a digest of the content, not the
content itself, so a secret typed via {tooliconl}`send-keys` or
{tooliconl}`send-keys-batch` does not land in that audit record.

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

[MCP defines](https://modelcontextprotocol.io/specification/2026-07-28/schema#toolannotations)
four standard hints for the behavior of the whole tool call.
[Clients may use positive hints](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/)
to skip confirmation or retry a call. This server can target an
existing tmux server selected by each call, and it cannot establish that the
server has no aliases or hooks or that another client will not add them. Every
tool that requests a tmux operation therefore advertises the same conservative
static hints:

| readOnlyHint | destructiveHint | idempotentHint | openWorldHint |
| --- | --- | --- | --- |
| false | true | false | true |

These values are hints, not authorization. They do not say that every call
modifies state, destroys data, has an additional effect when repeated, or
reaches outside tmux. They decline to promise otherwise for every target. The
project-owned `inspect`, `manage`, `execute`, and `teardown` toolsets preserve
the direct-operation distinctions that the standard hints cannot express here.
Clients that ignore project tags cannot recover those distinctions from the
four hints alone; they must use the tool name, schema, description, or an
operator-selected tool surface.

The optional `list_prompts` and `get_prompt` adapter tools do not contact tmux.
They belong to `inspect` because they render server-local prompt text without
changing tmux, and advertise `true`, `false`, `true`, `false` respectively.
