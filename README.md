# libtmux-mcp

MCP (Model Context Protocol) server for tmux, powered by [libtmux](https://github.com/tmux-python/libtmux).

[![Python Version](https://img.shields.io/pypi/pyversions/libtmux-mcp.svg)](https://pypi.org/project/libtmux-mcp/)
[![PyPI Version](https://img.shields.io/pypi/v/libtmux-mcp.svg)](https://pypi.org/project/libtmux-mcp/)
[![License](https://img.shields.io/github/license/tmux-python/libtmux-mcp.svg)](https://github.com/tmux-python/libtmux-mcp/blob/master/LICENSE)

> [!WARNING]
> **Pre-alpha.** APIs may change. Contributions and feedback welcome.

Give AI agents (Claude Code, Claude Desktop, Codex CLI, Gemini CLI, Cursor) programmatic control over tmux sessions - list, create, send keys, capture output, resize, and more.

## Quick start

### One-liner setup (no clone needed)

`uvx` handles install, deps, and execution automatically:

**Claude Code:**

```console
$ claude mcp add libtmux -- uvx libtmux-mcp
```

**Codex CLI:**

```console
$ codex mcp add libtmux -- uvx libtmux-mcp
```

**Gemini CLI:**

```console
$ gemini mcp add libtmux uvx -- libtmux-mcp
```

**Cursor** does not have an `mcp add` CLI command - use the JSON config below.

### JSON config (all tools)

```json
{
    "mcpServers": {
        "libtmux": {
            "command": "uvx",
            "args": ["libtmux-mcp"],
            "env": {
                "LIBTMUX_SOCKET": "ai_workspace"
            }
        }
    }
}
```

| Tool | Config file | Format |
|------|-------------|--------|
| Claude Code | `.mcp.json` (project) or `~/.claude.json` (global) | JSON |
| Claude Desktop | `claude_desktop_config.json` | JSON |
| Codex CLI | `~/.codex/config.toml` | TOML (see below) |
| Gemini CLI | `~/.gemini/settings.json` | JSON |
| Cursor | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | JSON |

<details>
<summary>Codex CLI config.toml format</summary>

```toml
[mcp_servers.libtmux]
command = "uvx"
args = ["libtmux-mcp"]
```

</details>

### Install with pip / uv

```console
$ uv pip install libtmux-mcp
```

```console
$ pip install libtmux-mcp
```

## Development install

Clone and install in editable mode:

```console
$ git clone https://github.com/tmux-python/libtmux-mcp.git
```

```console
$ cd libtmux-mcp
```

```console
$ uv pip install -e "."
```

Run the server:

```console
$ libtmux-mcp
```

Code changes take effect immediately - no reinstall needed.

### Local checkout CLI setup

Point your tool at the local checkout via `uv --directory`:

**Claude Code:**

```console
$ claude mcp add --scope user libtmux -- uv --directory ~/work/python/libtmux-mcp run libtmux-mcp
```

**Codex CLI:**

```console
$ codex mcp add libtmux -- uv --directory ~/work/python/libtmux-mcp run libtmux-mcp
```

**Gemini CLI:**

```console
$ gemini mcp add --scope user libtmux uv -- --directory ~/work/python/libtmux-mcp run libtmux-mcp
```

**Cursor** - add to `~/.cursor/mcp.json`:

```json
{
    "mcpServers": {
        "libtmux": {
            "command": "uv",
            "args": [
                "--directory", "~/work/python/libtmux-mcp",
                "run", "libtmux-mcp"
            ]
        }
    }
}
```

<details>
<summary>Codex CLI config.toml format (local checkout)</summary>

```toml
[mcp_servers.libtmux]
command = "uv"
args = ["--directory", "~/work/python/libtmux-mcp", "run", "libtmux-mcp"]
```

</details>

## What's included

### Tools

| Module | Tools |
|--------|-------|
| **Server** | `list_sessions`, `create_session`, `kill_server`, `get_server_info` |
| **Session** | `list_windows`, `create_window`, `rename_session`, `kill_session` |
| **Window** | `list_panes`, `split_window`, `rename_window`, `kill_window`, `select_layout`, `resize_window` |
| **Pane** | `send_keys`, `capture_pane`, `resize_pane`, `kill_pane`, `set_pane_title`, `get_pane_info`, `clear_pane`, `search_panes`, `wait_for_text` |
| **Options** | `show_option`, `set_option` |
| **Environment** | `show_environment`, `set_environment` |

### `tmux://` resources

Browse the tmux hierarchy via URI patterns:

- `tmux://sessions` - All sessions
- `tmux://sessions/{name}` - Session detail with windows
- `tmux://sessions/{name}/windows` - Session's windows
- `tmux://sessions/{name}/windows/{index}` - Window detail with panes
- `tmux://panes/{id}` - Pane details
- `tmux://panes/{id}/content` - Pane captured content

### Safety tiers

Control which tools are available via `LIBTMUX_SAFETY` env var:

| Tier | Tools | Use case |
|------|-------|----------|
| `readonly` | List, capture, search, info | Monitoring, browsing |
| `mutating` (default) | + create, send_keys, rename, resize | Normal agent workflow |
| `destructive` | + kill_server, kill_session, kill_window, kill_pane | Full control |

### Architecture

```
src/libtmux_mcp/
    __init__.py           # Entry point: main()
    __main__.py           # python -m libtmux_mcp support
    server.py             # FastMCP instance
    _utils.py             # Server caching, resolvers, serializers, error handling
    models.py             # Pydantic output models
    middleware.py         # Safety tier middleware
    tools/
        server_tools.py   # list_sessions, create_session, kill_server, get_server_info
        session_tools.py  # list_windows, create_window, rename_session, kill_session
        window_tools.py   # list_panes, split_window, rename_window, kill_window, select_layout, resize_window
        pane_tools.py     # send_keys, capture_pane, resize_pane, kill_pane, set_pane_title, get_pane_info, clear_pane, search_panes, wait_for_text
        option_tools.py   # show_option, set_option
        env_tools.py      # show_environment, set_environment
    resources/
        hierarchy.py      # tmux:// URI resources
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `LIBTMUX_SOCKET` | tmux socket name (`-L`). Isolates the MCP server to a specific socket. |
| `LIBTMUX_SOCKET_PATH` | tmux socket path (`-S`). Alternative to socket name. |
| `LIBTMUX_TMUX_BIN` | Path to tmux binary. Useful for testing with different tmux versions. |
| `LIBTMUX_SAFETY` | Safety tier: `readonly`, `mutating` (default), or `destructive`. |

## Requirements

- Python 3.10+
- tmux >= 3.2a
- [libtmux](https://github.com/tmux-python/libtmux) >= 0.55.0
- [fastmcp](https://github.com/jlowin/fastmcp) >= 3.1.0

## Links

- **Documentation**: <https://libtmux-mcp.git-pull.com>
- **libtmux** (core library): <https://libtmux.git-pull.com>
- **tmuxp** (workspace manager): <https://tmuxp.git-pull.com>
- **Source**: <https://github.com/tmux-python/libtmux-mcp>
- **Issues**: <https://github.com/tmux-python/libtmux-mcp/issues>

## License

MIT
