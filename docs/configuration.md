(configuration)=

# Configuration

Runtime configuration for the libtmux-mcp server. For MCP client setup, see {ref}`clients`.

## Environment variables

```{envvar} LIBTMUX_SOCKET
```

tmux socket name (`-L`). Isolates the MCP server to a specific tmux socket.

- **Type:** string
- **Default:** (none — uses the default tmux socket)

```{envvar} LIBTMUX_SOCKET_PATH
```

tmux socket path (`-S`). Alternative to socket name for custom socket locations.

- **Type:** string
- **Default:** (none)

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

```{envvar} LIBTMUX_MCP_WAIT_MAX_SECONDS
```

Server ceiling on how long any one wait may block. Applies to
{tooliconl}`wait-for-text`, {tooliconl}`wait-for-channel`, and
{tooliconl}`run-command`.

- **Type:** float, seconds
- **Default:** `30.0`
- **Range:** clamped to `[1.0, 120.0]`

Clamp, never reject: an over-large caller `timeout` is not an error — the
tool honours the ceiling instead and reports the value it actually
enforced, so the agent learns the policy from the result rather than
from a failed call. {tooliconl}`wait-for-text` and
{tooliconl}`run-command` report it on `effective_timeout`;
{tooliconl}`wait-for-channel` names it in the returned message. Compare
against the `timeout` you passed to see a clamp.

A bad value warns and falls back to the default rather than failing
startup. See {ref}`waiting` for why the ceiling exists and which wait to
reach for.

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
                "LIBTMUX_SUPPRESS_HISTORY": "1"
            }
        }
    }
}
```

## Socket isolation

By default, the MCP server connects to the default tmux socket. Set {envvar}`LIBTMUX_SOCKET` to isolate AI agent activity from your personal tmux sessions:

```json
"env": { "LIBTMUX_SOCKET": "ai_workspace" }
```

The agent will only see sessions on the `ai_workspace` socket, not your personal sessions.

## All tools accept `socket_name`

Every tool accepts an optional `socket_name` parameter that overrides {envvar}`LIBTMUX_SOCKET` for that call. This allows agents to work across multiple tmux servers in a single session.
