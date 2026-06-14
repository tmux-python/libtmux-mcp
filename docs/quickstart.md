(quickstart)=

# Quickstart

One happy path from zero to a working tool invocation.

## 1. Install

Pick your MCP client and install method:

```{mcp-install}
```

See {ref}`clients` for dev-checkout setup, full config-file locations, and common pitfalls.

## 2. Verify

Ask your LLM:

```{admonition} Prompt
:class: prompt

List all my tmux sessions and show me what's running in each pane.
```

The agent will call `list_sessions`, then `list_panes` and `capture_pane` to inspect your workspace. You should see your tmux sessions, windows, and pane contents in the response.

## 3. Try it

Here are a few things to try:

```{admonition} Prompt
:class: prompt

Create a new tmux session called "workspace" with a window named "build".
```

```{admonition} Prompt
:class: prompt

Send `make test` to the pane in my build window, then wait for it to finish and capture the output.
```

```{admonition} Prompt
:class: prompt

Search all my panes for the word "error".
```

## How it works

When you say "run `make test` and show me the output", the agent follows a typed command pattern:

1. {tool}`run-command` — send the authored shell command, wait for completion, and return exit status plus output
2. Inspect the typed result's `exit_status`, `timed_out`, and `output` fields

This **run → inspect** sequence is the default workflow for commands
the agent authors. For custom shell composition outside
{tool}`run-command`, the lower-level escape hatch is
{tool}`send-keys` with `tmux wait-for -S <channel>` composed into the
payload, followed by {tool}`wait-for-channel`. For output the agent
does not author (third-party log lines, daemon prompts, interactive
supervisors), use {tool}`wait-for-text` or {tool}`wait-for-content-change`.

When you need to keep checking the same pane after that first read, switch to
{tool}`capture-since`: the first call returns a cursor, and follow-up calls
return only new pane output.

## Next steps

- {ref}`concepts` — Understand the tmux hierarchy and how tools target panes
- {ref}`configuration` — Environment variables and socket isolation
- {ref}`safety` — Control which tools are available
- {ref}`Tools <tools-overview>` — Browse all available tools
