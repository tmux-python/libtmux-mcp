(clients)=

# MCP Clients

Copy-pasteable configuration for every supported MCP client. If your client isn't listed, any tool supporting MCP stdio transport will work with the JSON config pattern.

## Claude Code

```console
$ claude mcp add libtmux -- uvx libtmux-mcp
```

Config file: `.mcp.json` (project) or `~/.claude.json` (global).

## Claude Desktop

Add to `claude_desktop_config.json`:

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

## Codex CLI

```console
$ codex mcp add libtmux -- uvx libtmux-mcp
```

<details>
<summary>config.toml format</summary>

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.libtmux]
command = "uvx"
args = ["libtmux-mcp"]
```

</details>

## Gemini CLI

```console
$ gemini mcp add libtmux uvx -- libtmux-mcp
```

Config file: `~/.gemini/settings.json` (JSON format, same schema as Claude Desktop).

## Cursor

Add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{
    "mcpServers": {
        "libtmux": {
            "command": "uvx",
            "args": ["libtmux-mcp"]
        }
    }
}
```

## MCP Inspector

For testing and debugging:

```console
$ npx @modelcontextprotocol/inspector
```

## Config file locations

| Client | Config file | Format |
|--------|-------------|--------|
| Claude Code | `.mcp.json` (project) or `~/.claude.json` (global) | JSON |
| Claude Desktop | `claude_desktop_config.json` | JSON |
| Codex CLI | `~/.codex/config.toml` | TOML |
| Gemini CLI | `~/.gemini/settings.json` | JSON |
| Cursor | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | JSON |

## Local checkout (development)

For live development, point your client at a local checkout via `uv --directory`:

**Claude Code:**

```console
$ claude mcp add \
    --scope user \
    libtmux -- \
    uv --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

<details>
<summary>Codex CLI / Gemini CLI / Cursor</summary>

**Codex CLI:**

```console
$ codex mcp add libtmux -- \
    uv --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

**Gemini CLI:**

```console
$ gemini mcp add \
    --scope user \
    libtmux uv -- \
    --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
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

</details>

## Common pitfalls

- **Absolute paths**: Some clients require absolute paths in config. Use `$HOME/...` or the full path instead of `~/...`.
- **Virtual environments**: If using pip install (not uvx), ensure the venv is activated or use `uv run`.
- **Socket isolation**: Set `LIBTMUX_SOCKET` in the `env` block to isolate the MCP server from your default tmux. See {ref}`configuration`.
