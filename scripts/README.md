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

### `--scope {user,project}` (Claude only)

Claude's `~/.claude.json` has two layers: a top-level `mcpServers`
fallback that any project without an override picks up, and per-project
`projects.<abs>.mcpServers` overrides. The flag selects which layer the
swap writes:

```console
$ uv run scripts/mcp_swap.py use-local --cli claude --scope project   # default
$ uv run scripts/mcp_swap.py use-local --cli claude --scope user      # global fallback
```

`--scope project` (the default) preserves pre-flag behaviour: writes
under `projects[<repo-abs-path>].mcpServers` and only that one project
sees the change. `--scope user` flips the top-level fallback so every
unrelated project directory picks up the local checkout too — useful
when you're branch-testing across many repos at once.

Codex, Cursor, and Gemini have no per-project layer in their config
files. The flag is silently coerced to `user` for them, so passing
`--scope` with a non-Claude `--cli` is harmless.

A single Claude install can hold both scopes simultaneously — separate
state entries, separate backups. `revert --scope user` restores only
the user-level fallback; the project entry stays. `revert` without
`--scope` rolls back every recorded scope for the targeted CLIs.

### Safety

- Every rewrite writes a timestamped backup (`<config>.bak.mcp-swap-<ts>`)
  before touching the file. Claude backups also embed the scope
  (`<config>.bak.mcp-swap-<ts>-{user,project}`) so two scope swaps in the
  same second don't collide.
- State is tracked in `~/.local/state/libtmux-mcp-dev/swap/state.json`
  (honours `XDG_STATE_HOME`) so `revert` knows which backup to restore
  per CLI, including the "added" case where Codex had no libtmux block
  before. Schema is v2 (keys are `cli:scope`); v1 state files migrate
  on load.
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
