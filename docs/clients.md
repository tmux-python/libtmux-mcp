(clients)=

# MCP Clients

Pick your client, install method, and config scope below — the snippet
updates accordingly. The scope row appears only for clients with more
than one scope (Claude Desktop is always user-level so it has no scope
row). The full table of file locations is at the bottom of the page.

```{mcp-install}
:variant: full
```

If your client isn't listed, any tool supporting MCP stdio transport
will work with the JSON config pattern shown for Claude Desktop or
Cursor. See {ref}`migration` for the recommended `tmux` registration
slug (existing `libtmux` registrations keep working).

## MCP Inspector

For testing and debugging:

```console
$ npx @modelcontextprotocol/inspector
```

## Config file locations

| Client | Config file | Format |
|--------|-------------|--------|
| Claude Code | `.mcp.json` (project) or `~/.claude.json` (local/user) | JSON |
| Claude Desktop | `claude_desktop_config.json` | JSON |
| Codex CLI | `~/.codex/config.toml` (user) or `.codex/config.toml` (project, manual) | TOML |
| Gemini CLI | `~/.gemini/settings.json` (user) or `.gemini/settings.json` (project) | JSON |
| Cursor | `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) | JSON |
| Grok CLI | `~/.grok/config.toml` (user) or `./.grok/config.toml` (project) | TOML |
| Antigravity | `~/.gemini/config/mcp_config.json` (global) | JSON |

## Local checkout (development)

For live development, point your client at a local checkout via `uv --directory`:

**Claude Code:**

```console
$ claude mcp add \
    --scope user \
    tmux -- \
    uv --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

<details>
<summary>Codex CLI / Gemini CLI / Grok CLI / Cursor / Antigravity</summary>

**Codex CLI:**

```console
$ codex mcp add tmux -- \
    uv --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

**Gemini CLI:**

```console
$ gemini mcp add \
    --scope user \
    tmux uv -- \
    --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

**Grok CLI:**

```console
$ grok mcp add \
    --scope user \
    tmux -- \
    uv --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

**Cursor** — add to `~/.cursor/mcp.json`:

```json
{
    "mcpServers": {
        "tmux": {
            "command": "uv",
            "args": [
                "--directory", "~/work/python/libtmux-mcp",
                "run", "libtmux-mcp"
            ]
        }
    }
}
```

**Antigravity** — add to `~/.gemini/config/mcp_config.json`:

```json
{
    "mcpServers": {
        "tmux": {
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
- **Virtual environments**: If using pip install, ensure the venv is activated or the `libtmux-mcp` binary is on your PATH.
- **Socket isolation**: Set `LIBTMUX_SOCKET` in the `env` block to isolate the MCP server from your default tmux. See {ref}`configuration`.
