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
| **Server** | `list_sessions`, `create_session`, `kill_server`, `get_server_info` |
| **Session** | `list_windows`, `get_session_info`, `create_window`, `rename_session`, `select_window`, `kill_session` |
| **Window** | `list_panes`, `get_window_info`, `split_window`, `rename_window`, `select_layout`, `resize_window`, `move_window`, `kill_window` |
| **Pane** | `send_keys`, `paste_text`, `capture_pane`, `snapshot_pane`, `search_panes`, `get_pane_info`, `wait_for_text`, `wait_for_content_change`, `display_message`, `select_pane`, `swap_pane`, `resize_pane`, `set_pane_title`, `clear_pane`, `pipe_pane`, `enter_copy_mode`, `exit_copy_mode`, `respawn_pane`, `kill_pane` |
| **Options** | `show_option`, `set_option` |
| **Environment** | `show_environment`, `set_environment` |

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
