(quickstart)=

# Quickstart

## Requirements

- Python 3.10+
- tmux >= 3.2a
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

### One-liner with uvx

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

### pip / uv pip

```console
$ uv pip install libtmux-mcp
```

```console
$ pip install libtmux-mcp
```

### Development install

```console
$ git clone https://github.com/tmux-python/libtmux-mcp.git
```

```console
$ cd libtmux-mcp
```

```console
$ uv pip install -e "."
```

## Configuration

### JSON config (all MCP clients)

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
| Codex CLI | `~/.codex/config.toml` | TOML |
| Gemini CLI | `~/.gemini/settings.json` | JSON |
| Cursor | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | JSON |

### Local checkout setup

Point your tool at a local checkout for live development:

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

**Cursor** — add to `~/.cursor/mcp.json`:

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

## Environment variables

| Variable | Purpose |
|----------|---------|
| `LIBTMUX_SOCKET` | tmux socket name (`-L`). Isolates the MCP server to a specific socket. |
| `LIBTMUX_SOCKET_PATH` | tmux socket path (`-S`). Alternative to socket name. |
| `LIBTMUX_TMUX_BIN` | Path to tmux binary. Useful for testing with different tmux versions. |
| `LIBTMUX_SAFETY` | Safety tier: `readonly`, `mutating` (default), or `destructive`. |

## Running the server

```console
$ libtmux-mcp
```

Or via Python module:

```console
$ python -m libtmux_mcp
```

## Testing with MCP Inspector

```console
$ npx @modelcontextprotocol/inspector
```
