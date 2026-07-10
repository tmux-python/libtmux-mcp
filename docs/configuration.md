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

```{envvar} LIBTMUX_SUPPRESS_HISTORY
```

Controls the MCP default for best-effort shell-history suppression. This setting applies when an MCP caller omits `suppress_history` from {tooliconl}`run-command`, {tooliconl}`create-session`, {tooliconl}`create-window`, {tooliconl}`split-window`, or {tooliconl}`respawn-pane`.

- **Type:** string flag
- **Default:** `0` (disabled)
- **Values:** `0`, `1`

Unset and `0` disable suppression; `1` enables it. Any other value fails server startup with `LIBTMUX_SUPPRESS_HISTORY must be unset, '0', or '1'`, without echoing the rejected value. Precedence is per call: an explicit `suppress_history` value wins, then {envvar}`LIBTMUX_SUPPRESS_HISTORY`, then the default `False`. Direct Python calls also default to `False`.

The tools apply that value in two different ways. {toolref}`run-command` prefixes one space to the grouped event that carries the caller's single-line command. When suppression is effective, a command containing a carriage return or line feed fails before tmux receives input; set `suppress_history=false` for intentional multiline input. Each of the four spawn tools copies and merges the caller's environment with shell-history controls before starting a process. A conflicting caller-supplied history value fails the call, names the environment variable without including the conflicting value, and is never retried without suppression.

An explicit `suppress_history=false` stops the tool from adding a prefix or new environment controls for that call. It does not remove controls that the target process already inherits from a session, parent environment, or shell startup file. The startup default never changes the raw-input behavior of {toolref}`send-keys`, {toolref}`send-keys-batch`, {toolref}`paste-text`, or {toolref}`paste-buffer`.

The server resolves this setting once during startup. After changing it, restart the MCP server, usually by reconnecting or restarting the MCP client. See {ref}`history-hygiene` for the shell-specific limits and {ref}`safety` for surfaces that history suppression does not hide.

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
