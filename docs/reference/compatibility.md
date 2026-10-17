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
| >= 3.3a | Core operations supported |
| 3.3 and earlier | Refused before requested effects |

Commands validate the selected daemon, including when the client executable is
newer. An absent endpoint validates the executable before startup. Owned raw
subprocesses and generated completion helpers retain a native release guard;
live dispatch cannot start a replacement daemon after losing the admitted one.
Unknown release reports are refused. `command-alias` redefinitions of the
library's metadata and guard built-ins remain outside this qualification.

Admission uses the operation's remaining subprocess budget. Cancelling an owned
async subprocess kills and reaps its metadata or effect child. Parent libtmux
calls delegated through worker threads retain their existing cancellation
semantics; this does not make those threads interruptible.

Higher requirements for individual features are documented separately from
the core minimum. Baseline polling does not require control mode.

## Dependencies

| Package | Required version |
|---------|-----------------|
| [libtmux](https://libtmux.git-pull.com/) | >= 0.62.0, < 1.0 |
| [FastMCP](https://github.com/jlowin/fastmcp) | >= 4.0.2, < 5.0.0 |
| [regex](https://github.com/mrabarnett/mrab-regex) | >= 2024.11.6 |

The libtmux backend must provide the coordinated 3.3a admission contract.
This branch's uv source override pins the exact implementation from
[Python core PR779](https://github.com/tmux-python/libtmux/pull/779), pending a
package release. The numeric dependency range alone does not identify that
contract. Startup and tool dispatch refuse an incompatible backend before
effects. Use `uv sync --all-extras --dev` for the coordinated development pair.

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
