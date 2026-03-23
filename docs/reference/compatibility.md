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
