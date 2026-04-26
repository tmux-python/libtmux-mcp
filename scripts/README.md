# scripts/

Developer utilities shipped with the repo but not part of the installed
package.

## `mcp_swap.py`

Swap the libtmux MCP server entry across every detected agent CLI
(Claude Code, Codex, Cursor, Gemini) so all four run the **local checkout**
instead of a pinned PyPI release. Useful when testing a branch or working
on the server itself.

### Usage

From the repo root:

```console
$ uv run scripts/mcp_swap.py detect      # which CLIs are installed?
$ uv run scripts/mcp_swap.py status      # what does each point at today?
$ uv run scripts/mcp_swap.py use-local --dry-run
$ uv run scripts/mcp_swap.py use-local   # rewrite configs (with backups)
$ uv run scripts/mcp_swap.py revert      # restore from backups
```

Or via `just`:

```console
$ just mcp-detect
$ just mcp-status
$ just mcp-use-local --dry-run
$ just mcp-use-local
$ just mcp-revert
```

### What `use-local` does

For each detected CLI, the libtmux entry (or equivalent — derived from
`pyproject.toml` project name, trailing `-mcp` stripped) is rewritten to:

```
command = "uv"
args    = ["--directory", "<repo-abs-path>", "run", "libtmux-mcp"]
```

This matches Claude's conventional dev form and takes advantage of `uv
run`'s automatic editable install — source edits flow through on the next
invocation with no reinstall step.

### Safety

- Every rewrite writes a timestamped backup (`<config>.bak.mcp-swap-<ts>`)
  before touching the file.
- State is tracked in `~/.local/state/libtmux-mcp-dev/swap/state.json`
  (honours `XDG_STATE_HOME`) so `revert` knows which backup to restore
  per CLI, including the "added" case where Codex had no libtmux block
  before.
- Writes are atomic (tempfile + `os.replace`) and re-validated by
  re-parsing; a bad write is rolled back immediately.
- `--dry-run` prints a unified diff and writes nothing.

### Scope

Covers four CLIs and their canonical **global** config paths:

| CLI    | Config                       | Format |
|--------|-------------------------------|--------|
| Claude | `~/.claude.json`              | JSON (per-project keying) |
| Codex  | `~/.codex/config.toml`        | TOML (format-preserving via `tomlkit`) |
| Cursor | `~/.cursor/mcp.json`          | JSON |
| Gemini | `~/.gemini/settings.json`     | JSON |

Claude's config is keyed per-project under the repo's absolute path — the
script writes only under the current repo's key, leaving other projects'
entries untouched.

#### Out of scope (use the CLI's native command)

- **Workspace / project-local configs** for Cursor and Gemini
  (`$PWD/.cursor/mcp.json`, `$PWD/.gemini/settings.json`). When
  workspace precedence matters, use `cursor mcp add` / `gemini mcp add`
  directly — workspace files take precedence over the global ones this
  script writes.
- **Custom binary install locations.** Detection is `shutil.which` plus
  the file existing at the configured global path. Homebrew, npm
  prefixes (`~/.npm-global/bin`), and post-migration paths
  (`~/.claude/local/claude`, `~/.gemini/local/gemini`) are picked up
  only when the binary is already on `PATH`.

### Extending to a new CLI

Add an entry to the `CLIS` table in `mcp_swap.py` and extend the three
per-CLI branches in `get_server` / `set_server` / `delete_server`. Tests
in `tests/test_mcp_swap.py` use a `fake_home` fixture that monkeypatches
`CLIS`, so the extension pattern is already established.
