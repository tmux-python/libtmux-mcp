(compatibility)=

# Compatibility

## Python versions

| Python | Status |
|--------|--------|
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

Owned program tools require tmux 3.8+. Use `get_server_capabilities` to inspect
the connected daemon before choosing them.

## Dependencies

This draft pins the unreleased core implementation in
[libtmux PR #833](https://github.com/tmux-python/libtmux/pull/833). Install from
this checkout; publication remains held until that core API is released.

| Package | Required version |
|---------|-----------------|
| [libtmux](https://libtmux.git-pull.com/) | @ git+https://github.com/tmux-python/libtmux.git@ff7a6552de1ae2f87a11584df4aa48b0547af95b |
| [AnyIO](https://anyio.readthedocs.io/) | >= 4.16.0, < 5.0 |
| [FastMCP](https://github.com/jlowin/fastmcp) | >= 4.0.2, < 5.0.0 |
| [regex](https://github.com/mrabarnett/mrab-regex) | >= 2024.11.6 |

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
