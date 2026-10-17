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

## Optional operations

Ordinary input, pane capture, output polling, completion channels and
`run_command` retain the 3.3a core floor. The selected daemon determines
eligibility for each operation.

| Operation | Requirement |
| --- | --- |
| `load_buffer`, which stages a named buffer, and named buffer deletion or rename | tmux 3.4+, before dispatch; a 3.3a daemon creates no buffer |
| Nonempty `paste_text` | tmux 3.4+, before loading its temporary buffer; failure cleanup needs exact named deletion |
| Empty `paste_text`, `show_buffer` of an existing buffer and direct `paste_buffer` | Retain 3.3a |
| Literal `select-layout` on 3.3a | Validate the preset or serialized header before dispatch; ordinary valid layouts remain available |

The backend must provide `libtmux.admission.require_commands`; startup and
tools refuse a libtmux without it before work. Owned subprocess helpers also
enforce the parent's literal command policy, which a package pin by itself
cannot cover for commands that bypass libtmux's dispatcher.

`run_command` sends an authored command to a pane and observes completion. It
keeps 3.3a support independently of the parent library's captured foreground
`run-shell` output, which requires 3.5. Polling tools do not acquire control.
These optional refusals do not raise the whole MCP server's minimum.

## Dependencies

| Package | Required version |
|---------|-----------------|
| [libtmux](https://libtmux.git-pull.com/) | >= 0.63.0, < 1.0 |
| [FastMCP](https://github.com/jlowin/fastmcp) | >= 4.0.2, < 5.0.0 |
| [regex](https://github.com/mrabarnett/mrab-regex) | >= 2024.11.6 |

The libtmux backend must publish `libtmux.admission`, including
`require_commands`, which first ships in libtmux 0.63.0; the dependency range
names that release, so an installer never selects the 0.62 series. Until
libtmux 0.63.0 is released, this branch's uv source override pins the
implementation from
[Python feature PR780](https://github.com/tmux-python/libtmux/pull/780) for
development; ordinary package installers ignore that override. Startup and tool
dispatch refuse a backend without those names. Release libtmux first, then drop
the source override and merge this change. Use `uv sync --all-extras --dev` for
the development pair.

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
