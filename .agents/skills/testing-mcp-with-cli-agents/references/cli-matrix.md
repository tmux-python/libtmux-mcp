# CLI matrix — per-CLI isolation, proofs, and gotchas

Verified 2026-07-24 by driving all six CLIs against a branch libtmux-mcp server on
isolated tmux sockets, each with a throwaway config; **claude, cursor, gemini, and
agy re-verified 2026-07-25**. No CLI needs `mcp_swap` or a real-config write to be
tested — but a throwaway config home isolates *config*, not credentials (lesson 9).
Full model-driven tool-call proof (agent calls a tool, isolated socket confirms the
mutation) was reached on five of the six — **codex, cursor, grok, claude, agy**.
Only **gemini** is unreachable, and for a vendor reason: it is no longer an
eligible client for individual accounts. Flags drift — re-verify with `<cli> --help` before trusting any invocation
below.

## Cross-cutting lessons (the transferable part)

1. **Isolate the config, never mutate it.** Every CLI exposes a config-home or
   project-config lever (table below). None requires `mcp_swap` for a test.
2. **When a run dies, check auth/account tier before blaming the harness — but
   check where the credentials live before declaring an auth wall.** gemini →
   `IneligibleTierError: This client is no longer supported for Gemini Code Assist
   for individuals`, a real vendor block; a claude run under a drained
   `ANTHROPIC_API_KEY` account → `Credit balance is too low`, an account state.
   Both are findings — stop spending. agy looked like the same thing and was not:
   its runs were timing out because the credential it needed was not in the config
   tree at all (lesson 10). A `-p` run that hangs to timeout is weak evidence;
   raise `--print-timeout` and locate the credential before writing the verdict
   down.
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
9. **Copy credentials into a throwaway config home — never symlink them.** A symlink
   isolates reads, not writes: agy refreshed its OAuth token *through* the symlink and
   overwrote the user's real `~/.gemini/antigravity-cli/antigravity-oauth-token` (the
   real file's mtime moved). The same run with a **copy** refreshed the sandbox copy
   and left the real file's mtime alone — both directions measured. Any recipe below
   that seeds auth by symlink carries the same write-through risk; copy the files, and
   diff the originals afterward.
10. **A config-home flag relocates config, not credentials — and the credentials may
    not be files.** Google documents agy as storing its OAuth credentials in the OS
    keyring (Secret Service/dbus on Linux, Keychain on macOS, Credential Manager on
    Windows), so `--gemini_dir` can never carry them. On WSL2, where there is no
    Secret Service, it falls back to a token file that *can* be copied — which is
    why the recipe below works there and may not elsewhere. Before concluding a CLI
    is unauthenticatable under isolation, find out whether its credential is a file
    at all.

## Quick matrix

| CLI | headless one-shot | config-isolation lever | cheapest discovery proof | approval bypass (non-interactive) | full model proof reached |
|---|---|---|---|---|---|
| claude | `claude -p` | `--mcp-config <f> --strict-mcp-config` (session only) | `-p --output-format stream-json` init event | `--permission-mode bypassPermissions` | yes |
| codex | `codex exec` | `CODEX_HOME` throwaway **or** `-c` overrides | `codex mcp get tmux` (parses config, no spawn) | `--dangerously-bypass-approvals-and-sandbox` | yes |
| cursor | `cursor-agent --print` | project `.cursor/mcp.json` (merged, not isolating) | headless `--approve-mcps` run (see trap) | `--force --approve-mcps` (omit `--mode`) | yes |
| gemini | `gemini -p` | project `.gemini/settings.json` from cwd | `gemini mcp list` | `--approval-mode yolo` (`--skip-trust`) | no — `IneligibleTierError`, CLI unsupported for individuals |
| grok | `grok -p` / `--single` | `GROK_HOME` **or** `mcp add --scope project` | `grok mcp doctor tmux --json` (real handshake) | `--permission-mode bypassPermissions` | yes |
| agy | `agy -p` | hidden `--gemini_dir <path>` (**credentials do not follow it — copy the token in**) | none short of a model call | `--dangerously-skip-permissions` | yes |
| opencode | not yet verified | not yet verified | not yet verified | not yet verified | not yet driven through this harness |
| pi | n/a — no MCP client (see below) | n/a | n/a | n/a | n/a |

## Per-CLI detail

### opencode and pi — registered in mcp_swap, not yet driven here

`mcp_swap` writes both, but neither has been taken through the tmux harness, so
the row above is blank rather than guessed. What is known from the source:

- **opencode** stores MCP servers under a top-level `mcp` key in
  `$XDG_CONFIG_HOME/opencode/opencode.jsonc`, as
  `{"type": "local", "command": [argv...], "environment": {...}}`. `command` is
  one array, not a command/args pair, and the env table is `environment` — an
  `env` key is dropped in silence, while a scalar `command` fails the whole
  config's decode and stops opencode starting. `opencode mcp add <name> -- <cmd>`
  is non-interactive once both a name and a `--` command are given.
- **pi** has no MCP client at all: its README says "No MCP", and the released
  build contains no MCP code. `~/.pi/agent/mcp.json` is a convention of the
  third-party `pi-mcp-adapter` extension. Until that package is installed,
  nothing reads what a swap writes, and there is no agent behavior to drive.

### codex — two isolation styles, both verified
- **Config-less (leanest):** a home dir containing only a **copy** of the real
  `auth.json`, no `config.toml`, plus `-c` overrides:
  `-c 'mcp_servers.tmux.command="uv"' -c 'mcp_servers.tmux.args=["--directory","<SERVER>","run","libtmux-mcp"]' -c 'mcp_servers.tmux.env.LIBTMUX_SOCKET="mcplab-codex-target"'`.
  No config file to diff back.
- **Copy-config:** `cp ~/.codex/config.toml <home>/`; copy `auth.json`; rewrite
  `[mcp_servers.tmux]`. Downside: **drags in the user's hooks/output-style**, which
  fire in the isolated session — prefer the `-c` style.
- **CORRECTION:** earlier revisions of this entry symlinked `auth.json`. It holds a
  refreshable token, so that is the same write-through shape that bit agy (lesson 9);
  not measured on codex, but copy it anyway.
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
  Re-confirmed working 2026-07-25 with `--print --force --approve-mcps`.
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

### agy (Antigravity) — full success, once the credential is COPIED in
- **Hidden `--gemini_dir <path>`** flag (not in `--help`; `agy --gemini_dir` errors
  "flag needs an argument", confirming it) relocates the entire `~/.gemini` tree —
  cleaner than a `HOME` override.
- Working recipe, verified 2026-07-25 on WSL2 (the reported window names matched the
  isolated socket's raw-`tmux` ground truth):
  1. **Copy** `~/.gemini/antigravity-cli/antigravity-oauth-token` into
     `<gdir>/antigravity-cli/`.
  2. Put the MCP config at **`<gdir>/antigravity/mcp_config.json`**:
     `{"mcpServers":{"tmux":{...,"env":{"LIBTMUX_SOCKET":"mcplab-agy-target"}}}}`.
  3. `agy -p '<prompt>' --gemini_dir <gdir> --dangerously-skip-permissions --print-timeout 8m`
     (prepend `PATH=<uv>:<node>:$PATH`; add `--log-file <log>` to debug).
- **Where the credentials actually live.** Google documents agy as keeping OAuth
  credentials in the **OS keyring** (Secret Service/dbus on Linux, Keychain on macOS,
  Credential Manager on Windows), which is why `--gemini_dir` relocates config but
  never auth. On WSL2 there is no Secret Service, so it falls back to the
  `antigravity-oauth-token` file above — copy that and isolation is complete. Expect
  to need the keyring route on a desktop Linux/macOS box. `oauth_creds.json` belongs
  to the **old gemini CLI** and does nothing for agy.
- **CORRECTION:** an earlier "agy is auth-blocked / interactive OAuth" verdict here
  was a **timeout artefact** of a too-short `--print-timeout` plus the wrong seeded
  credential, not an auth wall.
- **Never symlink the credentials in.** agy refreshed its OAuth token *through* the
  symlink and overwrote the user's real
  `~/.gemini/antigravity-cli/antigravity-oauth-token`; with a copy, the sandbox copy
  was refreshed and the real file's mtime never moved. A symlinked config home
  isolates reads, not writes.
- Gotchas: **no `mcp` verb at all** — MCP is configured only by editing
  `mcp_config.json`, and the *only* way to enumerate/exercise tools is a model call.
  Headless needs `--dangerously-skip-permissions` (or `--mode accept-edits`).
  `--print-timeout` defaults to 5m; a model turn that also spawns an MCP server can
  outrun it, so raise it (8m worked) and wrap in an outer `timeout` rather than
  cutting it short.

### claude — full success
- Verified 2026-07-25: `claude -p --mcp-config <file> --strict-mcp-config
  --permission-mode bypassPermissions '<prompt>'` completed a real MCP tool call — the
  window name it reported matched the isolated socket's ground truth.
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
  `env -u ANTHROPIC_API_KEY claude …` forces subscription auth. A run against an
  `ANTHROPIC_API_KEY` account returned `Credit balance is too low` — an account state,
  not a CLI or harness limitation.

### gemini — isolation proven, CLI no longer eligible
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
  discards real OAuth). Auth: as of 2026-07-25 the CLI hard-fails with
  `IneligibleTierError: This client is no longer supported for Gemini Code Assist for
  individuals`, pointing individuals at Antigravity — no model turn is reachable here,
  and it is the client, not just the tier, that is now the blocker.
