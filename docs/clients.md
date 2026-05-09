(clients)=

# MCP Clients

Copy-pasteable configuration for every supported MCP client. If your client isn't listed, any tool supporting MCP stdio transport will work with the JSON config pattern.

See {ref}`migration` for the recommended `tmux` registration slug
(existing `libtmux` registrations keep working).

## Claude Code

`````{tab} uvx
With [uv](https://docs.astral.sh/uv/) installed:

```console
$ claude mcp add tmux -- uvx libtmux-mcp
```
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```console
$ claude mcp add tmux -- pipx run libtmux-mcp
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then register:

```console
$ claude mcp add tmux -- libtmux-mcp
```
`````

Config file: `.mcp.json` (project) or `~/.claude.json` (global).

## Claude Desktop

Add to `claude_desktop_config.json`:

`````{tab} uvx
With [uv](https://docs.astral.sh/uv/) installed:

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
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```json
{
    "mcpServers": {
        "tmux": {
            "command": "pipx",
            "args": ["run", "libtmux-mcp"]
        }
    }
}
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then use this config:

```json
{
    "mcpServers": {
        "tmux": {
            "command": "libtmux-mcp"
        }
    }
}
```
`````

## Codex CLI

`````{tab} uvx
With [uv](https://docs.astral.sh/uv/) installed:

```console
$ codex mcp add tmux -- uvx libtmux-mcp
```
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```console
$ codex mcp add tmux -- pipx run libtmux-mcp
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then register:

```console
$ codex mcp add tmux -- libtmux-mcp
```
`````

<details>
<summary>config.toml format</summary>

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.tmux]
command = "uvx"
args = ["libtmux-mcp"]
```

</details>

## Gemini CLI

`````{tab} uvx
With [uv](https://docs.astral.sh/uv/) installed:

```console
$ gemini mcp add tmux uvx -- libtmux-mcp
```
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```console
$ gemini mcp add tmux pipx -- run libtmux-mcp
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then register:

```console
$ gemini mcp add tmux libtmux-mcp
```
`````

Config file: `~/.gemini/settings.json` (JSON format, same schema as Claude Desktop).

## Cursor

Add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

`````{tab} uvx
With [uv](https://docs.astral.sh/uv/) installed:

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
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```json
{
    "mcpServers": {
        "tmux": {
            "command": "pipx",
            "args": ["run", "libtmux-mcp"]
        }
    }
}
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then use this config:

```json
{
    "mcpServers": {
        "tmux": {
            "command": "libtmux-mcp"
        }
    }
}
```
`````

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
    tmux -- \
    uv --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

<details>
<summary>Codex CLI / Gemini CLI / Cursor</summary>

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

</details>

## Common pitfalls

- **Absolute paths**: Some clients require absolute paths in config. Use `$HOME/...` or the full path instead of `~/...`.
- **Virtual environments**: If using pip install, ensure the venv is activated or the `libtmux-mcp` binary is on your PATH.
- **Socket isolation**: Set `LIBTMUX_SOCKET` in the `env` block to isolate the MCP server from your default tmux. See {ref}`configuration`.
