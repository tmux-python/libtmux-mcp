(compatibility)=

# Compatibility

## Python versions

| Python | Status |
|--------|--------|
| 3.10 | Supported |
| 3.11 | Supported |
| 3.12 | Supported |
| 3.13 | Supported |
| 3.14 | Supported |
| PyPy | Supported |

## tmux versions

| tmux | Status |
|------|--------|
| >= 3.2a | Supported |
| < 3.2a | Not supported (libtmux requirement) |

Every tool behaves identically across that range, with one exception.

### `copy_selection` requires tmux >= 3.4

On tmux 3.2a and 3.3a, `copy-selection` kills the tmux **server** —
and every session on it — rather than failing. Four conditions have to
hold together, and all four hold in a default install:

| condition | safe alternative |
|---|---|
| tmux 3.2a or 3.3a | 3.4 and later are unaffected |
| a client is attached | detached servers survive |
| `set-clipboard` is on | the default is `external` on 3.2a..3.7c |
| the terminal advertises clipboard support | `xterm-256color` crashes; `screen-256color` does not |

That conjunction is exactly what the tool is for — reading a person's
selection means an attached client, in a real terminal, at default
settings — so the surviving configurations are the ones with nobody in
them. {tooliconl}`copy-selection` refuses below 3.4 and names the
version. Use {tooliconl}`capture-pane` there instead.

From tmux 3.6 the copy also passes `-C`, so reading a selection does not
overwrite the user's system clipboard. On 3.4 and 3.5 that flag does not
exist and the copy does reach the clipboard.

## Dependencies

| Package | Required version |
|---------|-----------------|
| [libtmux](https://libtmux.git-pull.com/) | >= 0.55.0, < 1.0 |
| [FastMCP](https://github.com/jlowin/fastmcp) | >= 3.1.0, < 4.0.0 |

## MCP clients

| Client | Tested | Transport |
|--------|--------|-----------|
| Claude Code | Yes | stdio |
| Claude Desktop | Yes | stdio |
| Codex CLI | Yes | stdio |
| Gemini CLI | Yes | stdio |
| Cursor | Yes | stdio |
| MCP Inspector | Yes | stdio |

## OS support

| Platform | Status |
|----------|--------|
| Linux | Supported |
| macOS | Supported |
| WSL2 | Supported |
| Windows (native) | Not supported (tmux requires Unix) |
