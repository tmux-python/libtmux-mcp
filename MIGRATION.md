# Migration

Breaking changes and how to move through them, newest first.

## Safety tiers become toolsets

`readonly`, `mutating` and `destructive` are gone. Tools belong to four
unordered toolsets named for what they do.

### Environment variables

| Before | After |
| --- | --- |
| `LIBTMUX_SAFETY=readonly` | `LIBTMUX_TOOLSETS=inspect` |
| `LIBTMUX_SAFETY=mutating` (the default) | `LIBTMUX_TOOLSETS=inspect,manage,execute` (the default) |
| `LIBTMUX_SAFETY=destructive` | `LIBTMUX_TOOLSETS=inspect,manage,execute,teardown` |

A server started with `LIBTMUX_SAFETY` set now fails at startup naming the
replacement. It is not ignored: a variable that silently stopped working
would leave you believing a surface was narrower than it is.

Two variables are new. `LIBTMUX_TOOLS` enables individual tools regardless
of toolset, and `LIBTMUX_EXCLUDE_TOOLS` refuses them regardless of every
enable above. An unknown name in any of the three fails startup.

### Surfaces the tiers could not express

The tiers accumulated upward, so every surface was a prefix of the ladder.
The toolsets are a set, so this is now legal:

```console
$ LIBTMUX_TOOLSETS=inspect,teardown libtmux-mcp
```

An agent that can look and clean up, but not type.

### Tool names

| Before | After |
| --- | --- |
| `call_readonly_tools_batch` | `call_read_tools_batch` |
| `call_mutating_tools_batch` | removed — call the tool directly |
| `call_destructive_tools_batch` | removed — call the tool directly |

A batch gives every nested call the wrapper's name, so a client rule keyed
on `kill_session` never fires for a `kill_session` run inside one. That is
tolerable for reads and not for writes.

### Which toolset a tool is in

`inspect`
: Every `list_*`, `get_*`, `show_*`, `capture_*`, `snapshot_pane`,
  `search_panes`, `find_pane_by_position`, `display_message`,
  `wait_for_text`, `call_read_tools_batch`.

`manage`
: `rename_*`, `select_*`, `resize_*`, `move_window`, `swap_pane`,
  `set_pane_title`, `enter_copy_mode`, `exit_copy_mode`,
  `wait_for_channel`, `signal_channel`, `load_buffer`.

`execute`
: `create_session`, `create_window`, `split_window`, `respawn_pane`,
  `run_command`, `send_keys`, `send_keys_batch`, `paste_text`,
  `paste_buffer`, `pipe_pane`, `set_option`, `set_environment`.

`set_option` and `set_environment` are here rather than in `manage`
because tmux runs some stored values later: a `#(...)` job in a status
format runs when tmux draws it and repeats on the status interval, and
`default-command` decides what every future pane runs.

`teardown`
: `kill_pane`, `kill_window`, `kill_session`, `kill_server`, `clear_pane`,
  `delete_buffer`.

### Documentation

The safety topic is now the trust page. The old URL redirects.

### MCP annotations

Every tool that requests a tmux operation now explicitly advertises
`readOnlyHint: false`,
`destructiveHint: true`, `idempotentHint: false`, and `openWorldHint: true`.
An existing tmux server can use aliases and hooks to replace or extend the
operation libtmux-mcp requests, so no stronger static promise holds for every
target. The optional prompt adapter tools render text without contacting tmux
and retain their narrower hints. Use the project-owned toolsets to distinguish
the direct operation libtmux-mcp requests.
