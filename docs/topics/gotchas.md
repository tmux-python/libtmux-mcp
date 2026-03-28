(gotchas)=

# Gotchas

Things that will bite you if you don't know about them in advance. For symptom-based debugging, see {ref}`troubleshooting <troubleshooting>`.

## Metadata vs. content

{tool}`list-panes` and {tool}`list-windows` search **metadata** — names, IDs, current command. They do not search what is displayed in the terminal.

To find text that is visible in terminals, use {tool}`search-panes`. To read what a specific pane shows, use {tool}`capture-pane`.

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

The capture above may return the terminal state **before** pytest runs. Use {tool}`wait-for-text` between them:

```json
{"tool": "send_keys", "arguments": {"keys": "pytest", "pane_id": "%0"}}
{"tool": "wait_for_text", "arguments": {"pattern": "passed|failed|error", "pane_id": "%0", "regex": true}}
{"tool": "capture_pane", "arguments": {"pane_id": "%0"}}
```

See {ref}`recipes` for the complete pattern.

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
