(gotchas)=

# Gotchas

Things that will bite you if you don't know about them in advance. For symptom-based debugging, see {ref}`troubleshooting <troubleshooting>`.

## Metadata vs. content

{tooliconl}`list-panes` and {tooliconl}`list-windows` search **metadata** — names, IDs, current command. They do not search what is displayed in the terminal.

To find text that is visible in terminals, use {tooliconl}`search-panes`. To read what a specific pane shows once, use {tooliconl}`capture-pane`; to keep watching that pane, use {tooliconl}`capture-since`.

This is the most common source of agent confusion. The server
instructions already warn about this, but it bears repeating: if a
user asks "which pane mentions error", the answer is
{toolref}`search-panes`, not {toolref}`list-panes`.

## Enter after control keys

When you call {tooliconl}`send-keys` with `keys: "C-c"`, it sends
Ctrl-C **and then presses Enter**. For control sequences, set
`enter: false`:

```json
{"tool": "send_keys", "arguments": {"keys": "C-c", "pane_id": "%0", "enter": false}}
```

The `enter` parameter defaults to `true`, which is correct for commands (`make test` + Enter) but wrong for control keys, partial input, or key sequences.

## Capturing immediately after input is a race condition

{tooliconl}`send-keys` returns immediately after sending keystrokes to
tmux. It does **not** wait for the command to execute or produce
output.

```json
{"tool": "send_keys", "arguments": {"keys": "pytest", "pane_id": "%0"}}
{"tool": "capture_pane", "arguments": {"pane_id": "%0"}}
```

The capture above may return the terminal state **before** pytest
runs. For an authored shell command, use {tooliconl}`run-command`
instead:

```json
{"tool": "run_command", "arguments": {"command": "pytest", "pane_id": "%0", "timeout": 60}}
```

For custom shell composition outside {tooliconl}`run-command`, compose
`tmux wait-for -S <channel>` into the command and block on
{tooliconl}`wait-for-channel`. For output the agent does not author
(third-party logs, daemon prompts, interactive supervisors), use
{tooliconl}`wait-for-text` or observe with {tooliconl}`capture-since`.
See {ref}`recipes` for complete patterns.

## Repeated captures resend old output

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

However, they reset when the tmux **server** restarts. Do not cache pane IDs across server restarts. After killing and recreating a session, re-discover pane IDs with {tooliconl}`list-panes`.

(history-hygiene)=

## Shell-history suppression is best effort

The `suppress_history` argument reduces one history-persistence path; it does not make terminal input secret. {tooliconl}`run-command` prepends one ASCII space to the grouped event that carries the caller's single-line command. When suppression is effective, a command containing a carriage return or line feed is rejected before tmux receives input; set `suppress_history=false` for intentional multiline input. For [Bash 5.3](https://github.com/tianon/mirror-bash/blob/bash-5.3/doc/bashref.texi#L7274-L7282), the event is skipped only when `HISTCONTROL` contains `ignorespace` or `ignoreboth`. `ignorespace` is not a Bash default, although system or user configuration may enable it.

[Zsh 5.9](https://github.com/zsh-users/zsh/blob/zsh-5.9/Doc/Zsh/options.yo) requires `HIST_IGNORE_SPACE` for the same prefix convention, and an ignored event can linger in internal history until the next event. [Fish 4.8](https://github.com/fish-shell/fish-shell/blob/4.8.0/doc_src/interactive.rst) normally keeps a leading-space command off disk but leaves it recallable until the next command. A custom [`fish_should_add_to_history`](https://github.com/fish-shell/fish-shell/blob/4.8.0/doc_src/cmds/fish_should_add_to_history.rst) function takes over that decision and can store leading-space commands, while [bracketed paste handling](https://github.com/fish-shell/fish-shell/blob/4.8.0/CHANGELOG.rst) can strip leading spaces from pasted text. Do not treat the prefix convention as protection for paste input.

The startup setting {envvar}`LIBTMUX_SUPPRESS_HISTORY` also supplies the omitted default for {toolref}`create-session`, {toolref}`create-window`, {toolref}`split-window`, and {toolref}`respawn-pane`. Those spawn tools copy and merge an empty `HISTFILE`, Bash's `HISTCONTROL`, and private-history variables into the new process environment. The result is deliberately described as best effort:

| Shell | Spawn controls | Remaining caveat |
|-------|----------------|------------------|
| Bash | Empty `HISTFILE`; add `ignorespace` unless `ignoreboth` is already present | The interactive process can retain in-memory history. |
| Zsh | Empty `HISTFILE` | The interactive process can retain in-memory history. |
| Fish | Empty `fish_history`; non-empty `fish_private_mode` | The interactive process can retain in-memory history. |

Startup files can override any of these environment values after the process begins, so the shell can resume writing history to disk. An explicit `suppress_history=false` prevents the tool from adding controls, but it cannot erase controls already inherited from a tmux session, parent environment, or startup file.

{tooliconl}`send-keys` and {tooliconl}`send-keys-batch` do not inherit `LIBTMUX_SUPPRESS_HISTORY`; their `suppress_history` values remain explicit and default to `false`. Enabling it adds one leading space, so do not use it for control keys such as `C-c`, TUI input, or partial text unless that byte is intentional. In a batch, choose suppression separately for each operation. Paste tools have no suppression argument: {toolref}`paste-text` and {toolref}`paste-buffer` add no history prefix, but the active program can still interpret the paste or its whitespace. See {ref}`safety` for pane output, capture, process, transcript, hook, and log surfaces that remain visible.

## Gemini CLI injects `wait_for_previous` into tool arguments

When Gemini CLI batches several tool calls in one turn, its scheduler merges the internal sequencing flag of the later calls into the MCP tool's arguments:

```json
{"tool": "get_pane_info", "arguments": {"wait_for_previous": true, "pane_id": "%0"}}
```

This has been observed with stock Gemini CLI behavior (no extensions involved). In local non-interactive `gemini -p` harness runs, batching can front-load the topic tool and the first MCP calls into one parallel turn, which is where the leaked flag usually appears; interactive sessions tend to schedule more sequentially and trigger it less often.

Tool schemas are strict (`additionalProperties: false`), so the call is rejected with a validation error — classified as expected (WARNING log, `expected: true` in the result's `_meta`) and carrying a suggestion that names the rejected argument and identifies `wait_for_previous` as a client scheduling flag to retry without. Gemini's model reads it, drops the flag, and retries successfully on its own.

The visible symptom is harmless noise: `Error executing tool mcp_tmux_<name>: ... reported an error` lines in Gemini's output for calls that then succeed on retry, and matching WARNING records in the server log. Similar reports in other MCP servers have handled this injected key by stripping it or whitelisting arguments against the schema ([MemPalace/mempalace#816](https://github.com/MemPalace/mempalace/issues/816)). This server deliberately keeps the rejection: silently dropping unknown arguments would also swallow genuine argument typos from every client — on a server with mutating and destructive tools, a mis-named flag (`enter` on {toolref}`send-keys`, say) must fail loudly, not run with defaults.
