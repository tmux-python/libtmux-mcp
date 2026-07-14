(configuration)=

# Configuration

Runtime configuration for the libtmux-mcp server. For MCP client setup, see {ref}`clients`.

## Environment variables

```{envvar} LIBTMUX_SOCKET
```

tmux socket name (`-L`). Isolates the MCP server to a specific tmux socket.

- **Type:** string
- **Default:** (none — follows the frozen caller socket inside tmux, then the
  tmux default outside tmux)

```{envvar} LIBTMUX_SOCKET_PATH
```

tmux socket path (`-S`). Alternative to socket name for custom socket locations.

- **Type:** string
- **Default:** (none — falls through to the configured name, frozen caller
  socket, then tmux default)

```{envvar} LIBTMUX_TMUX_BIN
```

Path to tmux binary. Useful for testing with different tmux versions.

- **Type:** string
- **Default:** `tmux`

```{envvar} LIBTMUX_SAFETY
```

Safety tier controlling which tools are available. See {ref}`safety`.

- **Type:** string
- **Default:** `mutating`
- **Values:** `readonly`, `mutating`, `destructive`

```{envvar} LIBTMUX_ALLOW_SERVER_START
```

Controls the MCP default for allowing {tooliconl}`create-session` to start a
tmux server when its target is not running. Starting a server launches a
long-lived background daemon and sources the user's tmux configuration.

- **Type:** string flag
- **Default:** `0` (disabled)
- **Values:** `0`, `1`

Unset and `0` deny the implicit daemon start; `1` allows it. Any other value
fails server startup with
`LIBTMUX_ALLOW_SERVER_START must be unset, '0', or '1'`, without echoing the
rejected value. An explicit `allow_server_start` value wins for each MCP call.
Direct Python calls always default to `False`.

When the effective value is `false` and no server is running,
{toolref}`create-session` fails without starting one. Pass
`allow_server_start=true` for an intentional per-call start. The returned
{class}`~libtmux_mcp.models.SessionInfo` sets
{attr}`~libtmux_mcp.models.SessionInfo.server_started` to `true` when a
no-start attempt proved the target absent and the call then succeeded on the
startup-enabled path. A no-start creation accepted by an existing server
returns `false`; the field does not claim stronger attribution across other
processes.

The server resolves {envvar}`LIBTMUX_ALLOW_SERVER_START` once during startup.
Restart the MCP server after changing it, usually by reconnecting or restarting
the MCP client. Per-call arguments take effect without a restart.

```{envvar} LIBTMUX_SUPPRESS_HISTORY
```

Controls the MCP default for lightweight, best-effort command-history suppression. This setting applies only when an MCP caller omits `suppress_history` from {tooliconl}`run-command`.

- **Type:** string flag
- **Default:** `1` (enabled)
- **Values:** `0`, `1`

Unset and `1` enable suppression; `0` disables it. Any other value fails server startup with `LIBTMUX_SUPPRESS_HISTORY must be unset, '0', or '1'`, without echoing the rejected value. An explicit `suppress_history` value wins for each MCP call. Direct Python calls default to `False`.

{toolref}`run-command` prefixes one space to the grouped event that carries your single-line command. When suppression is effective, a command containing a carriage return or line feed fails before tmux receives input; set `suppress_history=false` for intentional multiline input.

Process creation uses a separate control. {toolref}`create-session`, {toolref}`create-window`, {toolref}`split-window`, and {toolref}`respawn-pane` expose `suppress_persistent_history`, which defaults to `false` for MCP and direct Python calls and never inherits this startup setting. Setting it to `true` copies and merges best-effort no-disk history controls into the spawned environment. A conflicting caller-supplied history value fails the call, names the environment variable without including the conflicting value, and is never retried without suppression.

Leaving it `false` adds no history controls. That choice cannot remove inherited, session, or startup-file controls; the process can still receive them from tmux, your supplied `environment`, or a shell startup file. The startup default never changes the raw-input behavior of {toolref}`send-keys`, {toolref}`send-keys-batch`, {toolref}`paste-text`, or {toolref}`paste-buffer`.

The server resolves {envvar}`LIBTMUX_SUPPRESS_HISTORY` once during startup. Restart the MCP server only after changing this startup setting, usually by reconnecting or restarting the MCP client. Per-call arguments take effect without a restart. See {ref}`history-hygiene` for shell-specific limits and {ref}`safety` for surfaces that history suppression does not hide.

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
                "LIBTMUX_SAFETY": "readonly",
                "LIBTMUX_ALLOW_SERVER_START": "0",
                "LIBTMUX_SUPPRESS_HISTORY": "1"
            }
        }
    }
}
```

## Socket isolation

With no configured selector, a server launched inside tmux follows the frozen
caller socket; outside tmux it connects to the tmux default. Set
{envvar}`LIBTMUX_SOCKET` to isolate AI agent activity from your personal tmux
sessions:

```json
"env": { "LIBTMUX_SOCKET": "ai_workspace" }
```

The agent will only see sessions on the `ai_workspace` socket, not your personal sessions.

## Target selection

Target precedence is: explicit per-call selector, configured path, configured
name, frozen caller socket, tmux default. The configured path comes from
{envvar}`LIBTMUX_SOCKET_PATH`; the configured name comes from
{envvar}`LIBTMUX_SOCKET`.

When both `LIBTMUX_SOCKET*` settings are unset, inside tmux, the frozen caller
socket is selected. When both are unset outside tmux, the tmux default is
selected. A targeted tool's optional `socket_name` overrides the configured and
caller-derived selectors for that call, which lets agents work across multiple
servers. {tooliconl}`list-servers` is the discovery exception: pass
`extra_socket_paths` to include sockets outside the canonical scan.
