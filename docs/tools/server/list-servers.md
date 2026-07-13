# List servers

```{fastmcp-tool} server_tools.list_servers
```

**Use when** you need to discover other live tmux servers on this
machine — for example, when an agent's tools were configured for the
default server but the user is also running a separate tmux for a
side project.

**Avoid when** you already know the socket name or path you want to
target — pass it directly to the tool that needs it via `socket_name`.

**Side effects:** None. Readonly. Stale socket files are filtered
via a kernel-level UNIX `connect()` probe, keeping discovery responsive
when `tmux-<uid>/` contains orphaned socket files.

**Scope:** Only servers under `${TMUX_TMPDIR:-/tmp}/tmux-<uid>/` are
discovered by the canonical scan. Custom `tmux -S /some/path/...`
daemons that live outside that directory must be supplied via
`extra_socket_paths`.

Results arrive as a {class}`~libtmux_mcp.models.ServerPage`. The default
page uses `limit=100` and `offset=0`; `total` describes every discovered live
server before paging, and `truncated` tells you whether another offset remains.

**Example:**

```json
{
  "tool": "list_servers",
  "arguments": {}
}
```

Response:

```json
{
  "items": [
    {
      "is_alive": true,
      "socket_name": "ci-runner",
      "socket_path": null,
      "session_count": 1,
      "version": "3.6a"
    },
    {
      "is_alive": true,
      "socket_name": "default",
      "socket_path": null,
      "session_count": 3,
      "version": "3.6a"
    }
  ],
  "total": 2,
  "offset": 0,
  "limit": 100,
  "truncated": false
}
```

To include a custom-path daemon:

```json
{
  "tool": "list_servers",
  "arguments": {
    "extra_socket_paths": ["/path/to/tmux.sock"]
  }
}
```

Paths that do not exist, are not sockets, or have no listener are
silently skipped.

```{fastmcp-tool-input} server_tools.list_servers
```

## Act
