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
via a kernel-fast UNIX `connect()` probe so the call stays under one
second even on machines with thousands of orphaned `tmux-<uid>/`
inodes.

**Scope:** Only servers under `${TMUX_TMPDIR:-/tmp}/tmux-<uid>/` are
discovered by the canonical scan. Custom `tmux -S /some/path/...`
daemons that live outside that directory must be supplied via
`extra_socket_paths`.

**Example:**

```json
{
  "tool": "list_servers",
  "arguments": {}
}
```

Response:

```json
[
  {
    "is_alive": true,
    "socket_name": "default",
    "socket_path": null,
    "session_count": 3,
    "version": "3.6a"
  },
  {
    "is_alive": true,
    "socket_name": "ci-runner",
    "socket_path": null,
    "session_count": 1,
    "version": "3.6a"
  }
]
```

To include a custom-path daemon:

```json
{
  "tool": "list_servers",
  "arguments": {
    "extra_socket_paths": ["/home/user/.cache/tmux/socket"]
  }
}
```

Paths that do not exist, are not sockets, or have no listener are
silently skipped.

```{fastmcp-tool-input} server_tools.list_servers
```

## Act
