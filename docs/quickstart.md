(quickstart)=

# Quickstart

One happy path from zero to a working tool invocation.

## 1. Install

Pick your MCP client and install method:

`````````{tab} Claude Code
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
`````````

`````````{tab} Claude Desktop
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
`````````

`````````{tab} Codex CLI
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

Config file: `~/.codex/config.toml` (TOML format — see {ref}`clients` for the schema).
`````````

`````````{tab} Gemini CLI
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

Config file: `~/.gemini/settings.json` (same JSON schema as Claude Desktop).
`````````

`````````{tab} Cursor
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
`````````

See {ref}`clients` for dev-checkout setup, full config-file locations, and common pitfalls.

## 2. Verify

Ask your LLM:

```{admonition} Prompt
:class: prompt

List all my tmux sessions and show me what's running in each pane.
```

The agent will call `list_sessions`, then `list_panes` and `capture_pane` to inspect your workspace. You should see your tmux sessions, windows, and pane contents in the response.

## 3. Try it

Here are a few things to try:

```{admonition} Prompt
:class: prompt

Create a new tmux session called "workspace" with a window named "build".
```

```{admonition} Prompt
:class: prompt

Send `make test` to the pane in my build window, then wait for it to finish and capture the output.
```

```{admonition} Prompt
:class: prompt

Search all my panes for the word "error".
```

## How it works

When you say "run `make test` and show me the output", the agent executes a three-step pattern:

1. {tool}`send-keys` — send the command to a tmux pane
2. {tool}`wait-for-text` — wait for the shell prompt to return (command finished)
3. {tool}`capture-pane` — read the terminal output

This **send → wait → capture** sequence is the fundamental workflow. Most agent interactions with tmux follow this pattern or a variation of it.

## Next steps

- {ref}`concepts` — Understand the tmux hierarchy and how tools target panes
- {ref}`configuration` — Environment variables and socket isolation
- {ref}`safety` — Control which tools are available
- {ref}`Tools <tools-overview>` — Browse all available tools
