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

When you say "run `make test` and show me the output", the agent executes a three-step pattern:

1. {tool}`send-keys` — send the command (composed with `tmux wait-for -S <channel>`) to a tmux pane
2. {tool}`wait-for-channel` — block deterministically until the command signals completion
3. {tool}`capture-pane` — read the terminal output

This **send → wait → capture** sequence is the fundamental workflow. For commands the agent authors, the channel pattern is deterministic; for output the agent does not author (third-party log lines, daemon prompts, interactive supervisors), substitute {tool}`wait-for-text` for step 2.

## Next steps

- {ref}`concepts` — Understand the tmux hierarchy and how tools target panes
- {ref}`configuration` — Environment variables and socket isolation
- {ref}`safety` — Control which tools are available
- {ref}`Tools <tools-overview>` — Browse all available tools
