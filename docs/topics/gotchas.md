(gotchas)=

# Gotchas

Things that will bite you if you don't know about them in advance. For symptom-based debugging, see {ref}`troubleshooting <troubleshooting>`.

## Metadata vs. content

{tooliconl}`list-panes` and {tooliconl}`list-windows` search **metadata** — names, IDs, current command. They do not search what is displayed in the terminal.

To find text that is visible in terminals, use {tooliconl}`search-panes`. To read what a specific pane shows once, use {tooliconl}`capture-pane`; to keep watching that pane, use {tooliconl}`capture-since`.

This is the most common source of agent confusion. The server instructions already warn about this, but it bears repeating: if a user asks "which pane mentions error", the answer is `search_panes`, not `list_panes`.

## `send_keys` sends Enter by default

When you call `send_keys` with `keys: "C-c"`, it sends Ctrl-C **and then presses Enter**. For control sequences, set `enter: false`:

```json
{"tool": "send_keys", "arguments": {"keys": "C-c", "pane_id": "%0", "enter": false}}
```

The `enter` parameter defaults to `true`, which is correct for commands (`make test` + Enter) but wrong for control keys, partial input, or key sequences.

## `capture_pane` after `send_keys` is a race condition

`send_keys` returns immediately after sending keystrokes to tmux. It does **not** wait for the command to execute or produce output.

```json
{"tool": "send_keys", "arguments": {"keys": "pytest", "pane_id": "%0"}}
{"tool": "capture_pane", "arguments": {"pane_id": "%0"}}
```

The capture above may return the terminal state **before** pytest runs. Compose `tmux wait-for -S <channel>` into the command and block on {tooliconl}`wait-for-channel` — deterministic, race-free:

```json
{"tool": "send_keys", "arguments": {"keys": "pytest; tmux wait-for -S pytest_done", "pane_id": "%0"}}
{"tool": "wait_for_channel", "arguments": {"channel": "pytest_done", "timeout": 60}}
{"tool": "capture_pane", "arguments": {"pane_id": "%0"}}
```

For output the agent does not author (third-party logs, daemon prompts, interactive supervisors), substitute {tooliconl}`wait-for-text` for `wait_for_channel`. See {ref}`recipes` for the complete pattern.

## Repeated `capture_pane` calls resend old output

If you are tailing a pane or checking a long-running process over several
turns, repeated {tooliconl}`capture-pane` calls keep returning the same visible
screen and scrollback. Use {tooliconl}`capture-since` instead: the first call
returns a cursor, and follow-up calls return only output written or rewritten
after that cursor. If tmux has already trimmed or cleared the needed history,
the result marks `lines_missed=true` and gives you a fresh cursor.

## Window names are not unique across sessions

Two sessions can each have a window named "editor". Targeting by `window_name` alone is ambiguous — always include `session_name` or use the globally unique `window_id` (e.g., `@0`, `@1`).

Pane IDs (`%0`, `%1`, etc.) are globally unique and are the preferred targeting method.

## Pane IDs are globally unique but ephemeral

Pane IDs like `%0`, `%5`, `%12` are unique across all sessions and windows within a tmux server. They do not reset when windows are created or destroyed.

However, they reset when the tmux **server** restarts. Do not cache pane IDs across server restarts. After killing and recreating a session, re-discover pane IDs with {ref}`list-panes`.

## `suppress_history` requires shell support

The `suppress_history` parameter on `send_keys` prepends a space before the command, which prevents it from being saved in shell history. This only works if the shell's `HISTCONTROL` variable includes `ignorespace` (the default for bash, but not universal across all shells).

## `clear_pane` is not fully atomic

`clear_pane` runs two tmux commands in sequence: `send-keys -R` (reset terminal) then `clear-history` (clear scrollback). There is a brief gap between them where partial content may be visible.

For most use cases this is not a problem. If you need guaranteed clean state, add a small delay before the next `capture_pane`.

## Gemini CLI injects `wait_for_previous` into tool arguments

When Gemini CLI batches several tool calls in one turn, its scheduler merges the internal sequencing flag of the later calls into the MCP tool's arguments:

```json
{"tool": "get_pane_info", "arguments": {"wait_for_previous": true, "pane_id": "%0"}}
```

This is stock Gemini CLI behavior (no extensions involved). Batching — and therefore the leak — is near-constant in non-interactive `gemini -p` runs, where the harness front-loads its topic tool and the first MCP calls into one parallel turn; interactive sessions schedule more sequentially and rarely trigger it.

Tool schemas are strict (`additionalProperties: false`), so the call is rejected with a validation error — classified as expected (WARNING log, `expected: true` in the result's `_meta`) and carrying a suggestion that names the rejected argument and identifies `wait_for_previous` as a client scheduling flag to retry without. Gemini's model reads it, drops the flag, and retries successfully on its own.

The visible symptom is harmless noise: `Error executing tool mcp_tmux_<name>: ... reported an error` lines in Gemini's output for calls that then succeed on retry, and matching WARNING records in the server log. Other MCP servers crash outright on this injection ([MemPalace/mempalace#816](https://github.com/MemPalace/mempalace/issues/816)) and patched it by stripping the key or whitelisting arguments against the schema. This server deliberately keeps the rejection: silently dropping unknown arguments would also swallow genuine argument typos from every client — on a server with mutating and destructive tools, a mis-named flag (`enter` on `send_keys`, say) must fail loudly, not run with defaults.
