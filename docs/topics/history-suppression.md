(history-suppression)=
(history-hygiene)=

# History suppression

libtmux-mcp provides best-effort shell-history suppression for Bash, Zsh,
and Fish. For most single-line commands authored through MCP, you do not need
to change anything: `suppress_history` is enabled by default for omitted MCP
calls to {tooliconl}`run-command`. When you start a new shell and want stronger
best-effort no-disk controls, opt in with `suppress_persistent_history=true`.

Neither control makes a command secret. Shell configuration can override the
request, in-memory history can remain available, and terminal output or other
observers can still record the command. See {ref}`safety` before handling
credentials.

## Why raw input stays explicit

Using {tooliconl}`send-keys` for every command an agent authors can fill an
interactive shell's history with orchestration noise. libtmux-mcp deliberately
stays out of the way on raw input: {tooliconl}`send-keys` and
{tooliconl}`send-keys-batch` preserve the caller's keystrokes, do not inherit
the server history default, and keep their own `suppress_history` arguments
off unless the caller opts in.

That literal behavior is necessary for control keys, partial text, REPLs, and
TUIs. For an authored single-line shell command, prefer
{tooliconl}`run-command`; it provides completion and output as one typed result
and requests lightweight history suppression by default.

## Choose the control

### One authored command

Use `suppress_history` on {tooliconl}`run-command`. It is enabled when omitted
by an MCP caller and applies to one command event in the existing shell.

### A newly spawned shell

Use `suppress_persistent_history=true` on {tooliconl}`create-session`,
{tooliconl}`create-window`, {tooliconl}`split-window`, or
{tooliconl}`respawn-pane`. This stronger no-disk control is disabled by
default, so you opt in per call. It applies to the new session environment or
to the one process being spawned, depending on the tool.

The two controls are independent. {envvar}`LIBTMUX_SUPPRESS_HISTORY` changes
only the omitted MCP default for {toolref}`run-command`; it never enables the
spawn control. Direct Python calls default both arguments to `False`.

## Default suppression for authored commands

{tooliconl}`run-command` prepends one ASCII space to the grouped event that
carries your single-line command. {envvar}`LIBTMUX_SUPPRESS_HISTORY` defaults
to `1`, so MCP calls that omit `suppress_history` request this lightweight
suppression. An explicit argument always wins.

One prefix cannot protect several shell events. When suppression is enabled,
a command containing a carriage return or line feed fails before tmux receives
input. Pass `suppress_history=false` when multiline input is intentional. The
{ref}`configuration` page covers startup values, validation, and restart
behavior.

## Bash, Zsh, and Fish behavior

The leading-space convention depends on the shell already running in the
pane:

- [Bash 5.3](https://github.com/tianon/mirror-bash/blob/bash-5.3/doc/bashref.texi#L7274-L7282)
  skips the event only when `HISTCONTROL` contains `ignorespace` or
  `ignoreboth`. `ignorespace` is not a Bash default, although system or user
  configuration may enable it.
- [Zsh 5.9](https://github.com/zsh-users/zsh/blob/zsh-5.9/Doc/Zsh/options.yo)
  requires `HIST_IGNORE_SPACE`. An ignored event can remain in internal
  history until the next event.
- [Fish 4.8](https://github.com/fish-shell/fish-shell/blob/4.8.0/doc_src/interactive.rst)
  normally keeps a leading-space command off disk but leaves it recallable
  until the next command. A custom
  [`fish_should_add_to_history`](https://github.com/fish-shell/fish-shell/blob/4.8.0/doc_src/cmds/fish_should_add_to_history.rst)
  function can store it, and
  [bracketed paste handling](https://github.com/fish-shell/fish-shell/blob/4.8.0/CHANGELOG.rst)
  can strip leading spaces from pasted text.

These are shell conventions, not an isolation boundary. Startup files and
interactive configuration remain authoritative.

## Opt into stronger controls for a new shell

Set `suppress_persistent_history=true` when you are about to spawn a Bash,
Zsh, or Fish shell and want stronger best-effort no-disk controls. The spawn
tools copy and merge history settings into the new environment:

### Bash

The spawned environment uses an empty `HISTFILE` and adds `ignorespace` to
`HISTCONTROL` unless `ignoreboth` is already present. The interactive process
can still retain in-memory history.

### Zsh

The spawned environment uses an empty `HISTFILE`. The interactive process can
still retain in-memory history.

### Fish

The spawned environment uses an empty `fish_history` and a non-empty
`fish_private_mode`. The interactive process can still retain in-memory
history.

Scope follows the tmux object you create:

- {tooliconl}`create-session` stores the controls in the new session
  environment, so the initial pane and future panes inherit them.
- {tooliconl}`create-window`, {tooliconl}`split-window`, and
  {tooliconl}`respawn-pane` apply the controls only to the process started by
  that call. They do not change the tmux session environment.

If you also supply `environment`, any history-control values must agree with
the requested policy. A conflict fails the call, names the variable without
including its value, and is never retried without suppression. Startup files
can still replace the merged values after the process starts. Leaving the
option `false` adds no controls and cannot remove settings inherited from
tmux, the caller, or a startup file.

## Raw input and paste stay explicit

{tooliconl}`send-keys` and {tooliconl}`send-keys-batch` do not inherit
{envvar}`LIBTMUX_SUPPRESS_HISTORY`; their `suppress_history` arguments remain
explicit and default to `false`. A leading space is usually wrong for control
keys such as `C-c`, TUI input, or partial text. In a batch, choose suppression
separately for each operation.

Paste tools have no suppression argument. {toolref}`paste-text` and
{toolref}`paste-buffer` add no history prefix, and the active program can
interpret the paste or its whitespace.

## What remains visible

History suppression does not clear pane echo, scrollback, in-memory history,
process arguments, tmux environment state, MCP client transcripts, hooks, or
logs. Prefer credential references that the child process resolves over
literal credentials in `command`, `keys`, `text`, `shell`, or `environment`.
See {ref}`safety` for the full observation boundary and {ref}`logging` for
audit-record behavior.
