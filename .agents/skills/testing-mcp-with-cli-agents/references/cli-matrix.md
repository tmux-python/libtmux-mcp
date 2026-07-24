# CLI matrix — per-CLI isolation, proofs, and gotchas

Verified 2026-07-24 by driving all six CLIs against a branch libtmux-mcp server on
isolated tmux sockets, each with a throwaway config. **Every real config was
confirmed byte-identical before/after** — no CLI needs `mcp_swap` or a real-config
write to be tested. Full model-driven tool-call proof (agent calls a tool, isolated
socket confirms the mutation) was reached on **codex, cursor, grok, agy**; **claude**
and **gemini** were blocked at account tier/credit, not by the harness. Flags drift —
re-verify with `<cli> --help` before trusting any invocation below.

## Cross-cutting lessons (the transferable part)

1. **Isolate the config, never mutate it.** Every CLI exposes a config-home or
   project-config lever (table below). None requires `mcp_swap` for a test.
2. **The wall is auth/account tier, not the harness.** claude → `Credit balance is
   too low`; gemini → `IneligibleTierError` (free tier unsupported). Treat these as
   findings and stop spending; they are not harness failures.
3. **Name the throwaway server distinctively (`tmuxlab`, not `tmux`).** All six CLIs
   already carry a `tmux` server (from a prior swap) pointing at the real checkout on
   the default socket. An identical name silently collides — it merges (cursor),
   shadows (gemini), or gets resolved instead of yours (claude `mcp list`). A unique
   name makes any leakage obvious.
4. **Config leaks across CLIs.** grok merges Claude Code's `~/.claude.json` *and* any
   cwd `.mcp.json` into its own MCP set; agy and gemini share the `~/.gemini` tree;
   codex's daemon is keyed to `CODEX_HOME` (so a throwaway home spawns an independent
   process tree — no conflict). Assume ambient servers are present unless you override
   `HOME`/config-home fully.
5. **"Cheapest proof" is not uniform.** grok's `mcp doctor` does a *real handshake*
   (reported 51 tools — matches the branch surface); codex's `mcp get` only *parses
   config*; agy has nothing short of a model call. Pick per CLI (table).
6. **PATH:** for a **headless** run, export the node + uv dirs once before invoking —
   the CLI inherits them and launches the `uv` server fine. The alternate-socket-pane
   PATH gap (a `-L` pane's non-login shell lacks the mise shims) only bites when you
   launch a CLI **TUI inside a harness pane** (Layer 2).
7. **Non-interactive mutating tool calls need an approval-bypass flag** — different per
   CLI (table). Without it, a mutating call blocks on an approval prompt with no TTY
   and the harness hangs.
8. **Interactive send-keys submit:** send the prompt text and `Enter` as **separate
   `send-keys` events** — then a single Enter submits. The "needs a double Enter"
   pitfall comes from batching text+Enter in one `send-keys` call. `Esc` cancels only
   **during** the working/tool phase; after a turn completes it enters edit-previous
   mode instead.

## Quick matrix

| CLI | headless one-shot | config-isolation lever | cheapest discovery proof | approval bypass (non-interactive) | full model proof reached |
|---|---|---|---|---|---|
| claude | `claude -p` | `--mcp-config <f> --strict-mcp-config` (session only) | `-p --output-format stream-json` init event | `--permission-mode bypassPermissions` | no — credit blocked |
| codex | `codex exec` | `CODEX_HOME` throwaway **or** `-c` overrides | `codex mcp get tmux` (parses config, no spawn) | `--dangerously-bypass-approvals-and-sandbox` | yes |
| cursor | `cursor-agent --print` | project `.cursor/mcp.json` (merged, not isolating) | headless `--approve-mcps` run (see trap) | `--force --approve-mcps` (omit `--mode`) | yes |
| gemini | `gemini -p` | project `.gemini/settings.json` from cwd | `gemini mcp list` | `--approval-mode yolo` (`--skip-trust`) | no — free tier |
| grok | `grok -p` / `--single` | `GROK_HOME` **or** `mcp add --scope project` | `grok mcp doctor tmux --json` (real handshake) | `--permission-mode bypassPermissions` | yes |
| agy | `agy -p` | hidden `--gemini_dir <path>` | none short of a model call | `--dangerously-skip-permissions` | yes |

## Per-CLI detail

### codex — two isolation styles, both verified
- **Config-less (leanest):** a home dir containing only a symlink to real `auth.json`,
  no `config.toml`, plus `-c` overrides:
  `-c 'mcp_servers.tmux.command="uv"' -c 'mcp_servers.tmux.args=["--directory","<SERVER>","run","libtmux-mcp"]' -c 'mcp_servers.tmux.env.LIBTMUX_SOCKET="mcplab-codex-target"'`.
  Nothing to copy or diff back.
- **Copy-config:** `cp ~/.codex/config.toml <home>/`; symlink `auth.json`; rewrite
  `[mcp_servers.tmux]`. Downside: **drags in the user's hooks/output-style**, which
  fire in the isolated session — prefer the `-c` style.
- Run: `env -u OPENAI_API_KEY CODEX_HOME=<home> codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C <SERVER> '<prompt>'`.
- Gotchas: **`OPENAI_API_KEY` in the env hijacks auth** to API-key billing even with a
  ChatGPT `auth.json` — always `env -u OPENAI_API_KEY` to use the subscription. **No
  `codex mcp list-tools`** exists; `mcp list`/`get`/`doctor` only count/parse config —
  real tool enumeration needs a model turn. Subcommand flags are position-sensitive
  (`--ignore-user-config`/`--skip-git-repo-check` go *after* the subcommand;
  `--skip-git-repo-check` is exec-only). `LIBTMUX_SOCKET` shows masked as `*****` in
  `mcp get`. The `/tmp` `CODEX_HOME` helper-binary warning prints on every subcommand —
  harmless.

### cursor — full success, and it CORRECTS prior art
- Project `<ws>/.cursor/mcp.json` with a **distinct** server name (`tmuxlab`); run with
  cwd=`<ws>` or `--workspace <ws>`:
  `cursor-agent --print --output-format stream-json --trust --approve-mcps --force --workspace <ws> '<prompt naming the tmuxlab tool>'`.
- **CORRECTION:** the old note "`mcp list-tools` returns tools even when `mcp list` says
  needs-approval" is **reversed** on build 2026.07.23 — `mcp list-tools <unapproved>`
  now *fails* (`has not been approved`), and `--approve-mcps` on the `mcp` subcommand
  doesn't help. Prove via a headless `--approve-mcps` agent run + the socket, not
  `list-tools`.
- **CORRECTION:** `--mode ask`/`--mode plan` are **read-only**, so a mutating call is
  suppressed. Omit `--mode` (default agent mode) and add `--force`.
- Project config is **merged** with global (all global servers still load) — isolate by
  unique name + `env.LIBTMUX_SOCKET`, not by expecting the project file to override.
  No `mcp add`; config is a JSON file only.

### grok — full success, best cheap proof
- `GROK_HOME=<ws>/.grok grok mcp add tmux -e LIBTMUX_SOCKET=mcplab-grok-target -- uv --directory <SERVER> run libtmux-mcp`, then
  `cp ~/.grok/auth.json ~/.grok/agent_id <ws>/.grok/` (**auth does not follow
  `GROK_HOME`**), then
  `GROK_HOME=<ws>/.grok grok -p '<prompt>' --permission-mode bypassPermissions --cwd <ws> --output-format plain`.
- `grok mcp doctor tmux --json` is the **best cheap proof of any CLI** — a real
  handshake reporting tool count, no model turn. Alternative isolation: `mcp add
  --scope project` writes `./.grok/config.toml` (keeps real `$HOME`/auth).
- Gotchas: **grok merges `~/.claude.json` + cwd `.mcp.json`** into its server set, so
  `GROK_HOME` alone doesn't fully isolate — override `HOME` for a clean set. `grok
  models` says "not authenticated" even when auth is valid (misleading banner; trust
  `doctor` and the run). `mcp add` prints the literal `$GROK_HOME` (cosmetic).

### agy (Antigravity) — full success, no `mcp` verb
- **Hidden `--gemini_dir <path>`** flag (not in `--help`; `agy --gemini_dir` errors
  "flag needs an argument", confirming it) relocates the entire `~/.gemini` tree —
  cleaner than a `HOME` override. Symlink the real auth/state files
  (`oauth_creds.json`, `google_account_id`, `installation_id`, `settings.json`, …)
  into `<gdir>`, but make `<gdir>/config/mcp_config.json` your own
  `{"mcpServers":{"tmux":{...,"env":{"LIBTMUX_SOCKET":"mcplab-agy-target"}}}}`.
- Run: `PATH=<uv>:<node>:$PATH agy --gemini_dir <gdir> --log-file <log> --dangerously-skip-permissions --print-timeout 3m -p '<prompt>'`.
- Gotchas: **no `mcp` verb at all** — MCP is configured only by editing
  `mcp_config.json`, and the *only* way to enumerate/exercise tools is a model call.
  `--gemini_dir` does **not** isolate auth (symlink it in). Headless needs
  `--dangerously-skip-permissions` (or `--mode accept-edits`). `--print-timeout`
  default is 5m — set it low and wrap in an outer `timeout`.

### claude — isolation proven, model turn credit-blocked
- `--mcp-config <file> --strict-mcp-config` fully scopes which MCP servers a
  `-p`/interactive **session** sees (the `init` event lists only your server) — but the
  server sits at `status:"pending"` and connects lazily on the **first model turn**, so
  you can't enumerate its tools without spending one.
- **`claude mcp list`/`get` ignore `--mcp-config`** and inspect the *ambient* config —
  not usable for isolated discovery. Use a `-p --output-format stream-json` run and read
  the `init` event's `mcp_servers` array.
- `--strict-mcp-config` scopes MCP **only**: a `-p` run still **writes `~/.claude.json`**
  (it grew) and creates `~/.claude/projects/<cwd>/`, and ambient hooks/skills/plugins
  fire. Add **`--bare`** to strip hooks/auto-memory/keychain/CLAUDE.md and minimize
  ambient writes. `mcp list` alone leaves `~/.claude.json` untouched.
- Auth: `ANTHROPIC_API_KEY` (if set) takes precedence over the claude.ai OAuth login;
  `env -u ANTHROPIC_API_KEY claude …` forces subscription auth. Here the API key's
  account returned `Credit balance is too low` — model turn blocked.

### gemini — isolation proven, model turn tier-blocked
- Project `<ws>/.gemini/settings.json` (`{"mcpServers":{"tmux":{"command","args","env"}}}`)
  read from **cwd**; a project-scoped server **shadows** a same-named user server (real
  `settings.json` stays clean). `gemini mcp add <name> <cmd> [args] -s project -e K=V`
  defaults to project scope.
- Run: `gemini --skip-trust --allowed-mcp-server-names tmux --approval-mode yolo --output-format json -p '<prompt>'` (verified against gemini 0.52.0).
- Gotchas: **untrusted folders disable ALL MCP** — `mcp list` shows every server
  `Disabled`; pass `--skip-trust` on the run (`mcp list` has no such flag, so its
  Disabled output is expected, not a failure). A failed headless run **still mutates
  `~/.gemini/projects.json`** (appends the cwd) — project config does not stop that
  global-registry write; full isolation needs a `HOME`/config-dir override (which
  discards real OAuth). Auth: `IneligibleTierError` free-tier — no model turn on this
  account/CLI version.
