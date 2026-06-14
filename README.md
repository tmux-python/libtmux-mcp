# libtmux-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server for [tmux](https://github.com/tmux/tmux), built on [libtmux](https://libtmux.git-pull.com).

[![Python Version](https://img.shields.io/pypi/pyversions/libtmux-mcp.svg)](https://pypi.org/project/libtmux-mcp/)
[![PyPI Version](https://img.shields.io/pypi/v/libtmux-mcp.svg)](https://pypi.org/project/libtmux-mcp/)
[![License](https://img.shields.io/github/license/tmux-python/libtmux-mcp.svg)](https://github.com/tmux-python/libtmux-mcp/blob/master/LICENSE)

> [!WARNING]
> **Pre-alpha.** APIs may change. Contributions and feedback welcome.

Give your AI agent hands inside the terminal — create sessions, run commands, read output, orchestrate panes.

## Tools

| Module | Tools |
|--------|-------|
| **Server** | `list_servers`, `list_sessions`, `create_session`, `kill_server`, `get_server_info` |
| **Session** | `list_windows`, `get_session_info`, `create_window`, `rename_session`, `select_window`, `kill_session` |
| **Window** | `list_panes`, `get_window_info`, `split_window`, `rename_window`, `select_layout`, `resize_window`, `move_window`, `kill_window` |
| **Pane** | `run_command`, `send_keys`, `send_keys_batch`, `paste_text`, `capture_pane`, `capture_since`, `snapshot_pane`, `search_panes`, `find_pane_by_position`, `get_pane_info`, `wait_for_text`, `wait_for_content_change`, `wait_for_channel`, `signal_channel`, `display_message`, `select_pane`, `swap_pane`, `resize_pane`, `set_pane_title`, `clear_pane`, `pipe_pane`, `enter_copy_mode`, `exit_copy_mode`, `respawn_pane`, `kill_pane` |
| **Options** | `show_option`, `set_option` |
| **Environment** | `show_environment`, `set_environment` |
| **Buffers** | `load_buffer`, `paste_buffer`, `show_buffer`, `delete_buffer` |
| **Hooks** | `show_hooks`, `show_hook` |

## Quickstart

**Requirements:** Python 3.10+, tmux on `$PATH`.

Install and run:

```bash
uvx libtmux-mcp
```

### Claude Code

```bash
claude mcp add tmux -- uvx libtmux-mcp
```

### Codex CLI

```bash
codex mcp add tmux -- uvx libtmux-mcp
```

### Gemini CLI

```bash
gemini mcp add tmux uvx -- libtmux-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tmux": {
      "command": "uvx",
      "args": ["libtmux-mcp"]
    }
  }
}
```

More clients and JSON config: [client setup docs](https://libtmux-mcp.git-pull.com/clients/)

## What it feels like

> **You:** Create a session called "api" and run `pytest tests/api/ -x` in it.
>
> **Agent:** Created session `api` with window `tests`. Running pytest now.
> Here's the output — 14 passed, 2 failed. The failures are in
> `test_auth.py::test_token_refresh` and `test_auth.py::test_expired_session`.
> Want me to open those files?

The agent manages tmux directly. No copy-pasting terminal output. No switching windows to check on long-running processes.

## When the server earns its keep

For a single `tmux send-keys`, the server doesn't. It earns its keep
the moment the agent has to wait, inspect, or avoid damaging the
terminal it is using — pytest finishing, a dev server printing its
port, a deploy log settling. The difference then is not more access
to tmux, but a better place to put the control loop.

The server-side moves are:

**Running.** [`run_command`](https://libtmux-mcp.git-pull.com/tools/pane/run-command/)
sends an authored shell command, waits for deterministic completion,
and returns exit status plus tail-preserved output as one typed value.
The alternative is teaching every agent to compose `send-keys`,
`wait-for`, and a pane capture correctly.

**Driving.** [`send_keys_batch`](https://libtmux-mcp.git-pull.com/tools/pane/send-keys-batch/)
sends several ordered raw-input operations for TUIs and persistent
shell interaction. It is deliberately not a workflow DSL; command
completion stays in `run_command`, and repeated observation stays in
`capture_since`.

**Waiting.** [`wait_for_text`](https://libtmux-mcp.git-pull.com/tools/pane/wait-for-text/)
and [`wait_for_content_change`](https://libtmux-mcp.git-pull.com/tools/pane/wait-for-content-change/)
block inside the server until a condition fires for output the agent
does not author. The alternative is the model polling `capture-pane`
in a loop, paying both context tokens and round-trip latency for every
turn.

**Reading.** [`snapshot_pane`](https://libtmux-mcp.git-pull.com/tools/pane/snapshot-pane/)
returns content, cursor, copy-mode state, and scroll offset as one
typed value. The alternative is several `tmux` invocations stitched
together with regex.

**Observing.** [`capture_since`](https://libtmux-mcp.git-pull.com/tools/pane/capture-since/)
returns a cursor with the current pane content, then returns only
newly written or rewritten rows on follow-up calls. The alternative is
re-sending the same scrollback to the model on every check.

**Guarding.** The server detects the agent's own pane across sockets
and declines self-destructive operations — [`kill_session`](https://libtmux-mcp.git-pull.com/tools/session/kill-session/)
on itself fails loudly instead of silently terminating the host
environment the agent is running in. [`LIBTMUX_SAFETY`](https://libtmux-mcp.git-pull.com/configuration/#envvar-LIBTMUX_SAFETY)
(`readonly`, `mutating`, `destructive`) hides whole tiers from the
client's tool list before any prompt is built.

## Documentation

Full docs, guides, and tool reference: **[libtmux-mcp.git-pull.com](https://libtmux-mcp.git-pull.com)**

## Development

Clone and install:

```bash
git clone https://github.com/tmux-python/libtmux-mcp.git
```

```bash
cd libtmux-mcp
```

```bash
uv sync --dev
```

Run the server locally:

```bash
uv run libtmux-mcp
```

Run tests:

```bash
uv run pytest
```

## Related projects

- [libtmux](https://libtmux.git-pull.com) — Python API for tmux
- [tmuxp](https://tmuxp.git-pull.com) — tmux session manager
- [The Tao of tmux](https://leanpub.com/the-tao-of-tmux) — the book

## License

MIT
