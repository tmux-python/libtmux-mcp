(clients)=

# MCP Clients

Copy-pasteable configuration for every supported MCP client. If your client isn't listed, any tool supporting MCP stdio transport will work with the JSON config pattern.

## Claude Code

`````{tab} uvx
With [uv](https://docs.astral.sh/uv/) installed:

```console
$ claude mcp add libtmux -- uvx libtmux-mcp
```
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```console
$ claude mcp add libtmux -- pipx run libtmux-mcp
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then register:

```console
$ claude mcp add libtmux -- libtmux-mcp
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
        "libtmux": {
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
        "libtmux": {
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
        "libtmux": {
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
$ codex mcp add libtmux -- uvx libtmux-mcp
```
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```console
$ codex mcp add libtmux -- pipx run libtmux-mcp
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then register:

```console
$ codex mcp add libtmux -- libtmux-mcp
```
`````

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

`````{tab} uvx
With [uv](https://docs.astral.sh/uv/) installed:

```console
$ gemini mcp add libtmux uvx -- libtmux-mcp
```
`````

`````{tab} pipx
With [pipx](https://pipx.pypa.io/) installed:

```console
$ gemini mcp add libtmux pipx -- run libtmux-mcp
```
`````

`````{tab} pip install
Install the packages first:

```console
$ pip install --user --upgrade libtmux libtmux-mcp
```

Then register:

```console
$ gemini mcp add libtmux libtmux-mcp
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
        "libtmux": {
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
        "libtmux": {
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
        "libtmux": {
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
- **Virtual environments**: If using pip install, ensure the venv is activated or the `libtmux-mcp` binary is on your PATH.
- **Socket isolation**: Set `LIBTMUX_SOCKET` in the `env` block to isolate the MCP server from your default tmux. See {ref}`configuration`.

## Known client issues

:::{warning}
**Cursor agent Auto Mode: string tool arguments may arrive unquoted.**

On some model paths, Cursor's agent serializes MCP tool arguments with string values that are missing their surrounding JSON quotes — producing payloads like `{"session_id": $10}` or `{"session_name": cv}` that fail at the transport's JSON-parse step *before* libtmux-mcp's Python handlers run. Symptom on the client side:

- `Unexpected token '$', "{"session_id": $10}" is not valid JSON`
- `Unexpected token 'c', "{"session_name": cv}" is not valid JSON`

**Mitigation shipped in libtmux-mcp**:

1. **Prompt-level.** The server's MCP `instructions` now carry an explicit "emit JSON-quoted strings" directive that the client surfaces to the model alongside tool schemas. Models that honor server-level MCP instructions should produce valid JSON.
2. **Transport-level.** libtmux-mcp wraps the MCP SDK's stdio transport with a conservative quote-repair layer (`libtmux_mcp._json_repair`). On each inbound frame, malformed `"key": VALUE` pairs where VALUE is a tmux ID (`$10`, `%1`, `@5`) or bare identifier (`cv`, `my.session`) are quoted before `json.loads` runs — so the tool call completes even if the client's model ignores the prompt directive. Valid JSON is a fixed point of the repair; JSON keywords (`true`, `false`, `null`) are never quoted; numbers are never touched.

The transport-level repair is implemented as a plain subclass of `FastMCP` (`libtmux_mcp._server_class.LibtmuxMcpServer`) that threads a wrapping stdin into the MCP SDK's public `stdio_server(stdin=...)` API — no monkey-patching of library internals. Operators who need the un-repaired stream (e.g. for reproducing client bugs cleanly) can opt out with `LIBTMUX_MCP_DISABLE_JSON_REPAIR=1` in the server's environment.

**Belt-and-suspenders**: prefer `pane_id` (e.g. `"%1"`) for pane targeting whenever possible — pane IDs are globally unique within a tmux server and are the recommended default anyway. See {doc}`topics/troubleshooting` for the broader targeting guidance.

Tracked at [tmux-python/libtmux-mcp#17](https://github.com/tmux-python/libtmux-mcp/issues/17).
:::
