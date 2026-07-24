# CLI matrix — last-known-good invocations and per-CLI traps

Flags drift between releases. Treat everything here as last-known-good and
re-verify with `<cli> --help` / `<cli> mcp --help` before trusting it. Sourced
from verified prior QA runs against libtmux-mcp and agentgrep-mcp.

## Registration proofs (cheapest — run before spending a full prompt)

```console
$ claude mcp list;         claude mcp get tmux
$ codex mcp list;          codex mcp get tmux
$ gemini mcp list
$ cursor-agent mcp list-tools tmux      # NOT `mcp list` — see trap below
```

## Headless one-shot (Layer 1)

```console
$ codex -a never exec --sandbox read-only --json -C /repo \
    'Call the tmux MCP: list sessions and report the count.'

$ gemini --skip-trust --allowed-mcp-server-names tmux --approval-mode yolo \
    --output-format json -p 'Call the tmux MCP: list sessions.'

$ cursor-agent --print --output-format stream-json --trust --approve-mcps \
    --sandbox enabled --mode ask --workspace /repo \
    'Call the tmux MCP: list sessions.'
```

Claude headless: `claude -p '<prompt>'` (add MCP scope/permission flags as your
setup requires). Grok and agy follow the interactive (Layer 2) path unless a
current `--help` shows a print/exec mode.

## Per-CLI traps

- **cursor-agent `mcp list` under-reports.** It can say `not loaded (needs
  approval)` while `cursor-agent mcp list-tools tmux` returns the full list and
  Composer calls the tools fine. `list-tools` + one real call is the
  authoritative proof, not the status string.
- **Codex approval-flag placement.** Use the top-level form `codex -a never
  exec ...`; passing the approval policy *after* `exec` fails.
- **Claude account state can block headless runs** (`Credit balance is too
  low`). That's a client/account limit, not a server bug — fall back to Layer 2
  or Layer 0.
- **Claude worktree scope shadowing.** Claude maps a linked git worktree to the
  main worktree path for per-project MCP, so a per-project entry can out-rank a
  `--scope user` swap. Know which layer you're actually hitting with `claude mcp
  get`.

## Native `mcp add` (per CLI) — for injecting env like LIBTMUX_SOCKET

mcp_swap preserves existing env but does not add new keys, so to sandbox the
socket use each CLI's own add command (syntax varies; the `-e` / `--env`
placement is the usual footgun):

```console
$ claude mcp add tmux -s user -e LIBTMUX_SOCKET=mcp-target -- uv --directory /repo run libtmux-mcp
$ codex  mcp add tmux --env LIBTMUX_SOCKET=mcp-target       -- uv --directory /repo run libtmux-mcp
$ gemini mcp add tmux -s user -e LIBTMUX_SOCKET=mcp-target  uv -- --directory /repo run libtmux-mcp
$ grok   mcp add tmux -e LIBTMUX_SOCKET=mcp-target          -- uv --directory /repo run libtmux-mcp
```

Cursor and agy have no `mcp add` — edit their JSON config directly
(`~/.cursor/mcp.json`, `~/.gemini/config/mcp_config.json`) with the same
`command` / `args` / `env` shape, then approve in-app.

## mcp_swap traps (this repo)

- **Pass `--server tmux`.** The script derives the slug `libtmux` from the
  package name, but the effective registered key on this machine is `tmux`.
  Without the flag you swap a `libtmux` entry nobody uses — `status` with no flag
  reports `no entry for 'libtmux'` across all six CLIs.
- **`--entry` when the first `[project.scripts]` key isn't the MCP entry.** Here
  the only script is `libtmux-mcp`, so the default is fine; other repos (e.g.
  agentgrep needed `--entry agentgrep-mcp`) do not get that for free.
- **`use-local` mutates real user configs.** Dry-run first; `revert` unwinds from
  timestamped backups in LIFO order when swaps stack.

## Direct-smoke sanity values (Layer 0, libtmux-mcp)

A healthy default-tier smoke against an isolated socket has looked like: `~52`
tools visible, `list_sessions` works, `call_readonly_tools_batch` works,
`send_keys_batch` preserves op order, and an oversized batch is rejected with
`operations must contain at most 1000 tool calls`. Exact tool count shifts as the
surface evolves — treat these as a shape check, not a fixed assertion.
