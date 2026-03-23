(quickstart)=

# Quickstart

One happy path from zero to a working tool invocation.

## 1. Install

```console
$ claude mcp add libtmux -- uvx libtmux-mcp
```

Using a different client? See {ref}`installation` and {ref}`clients`.

## 2. Verify

Ask your LLM:

> List all my tmux sessions and show me what's running in each pane.

The agent will call `list_sessions`, then `list_panes` and `capture_pane` to inspect your workspace. You should see your tmux sessions, windows, and pane contents in the response.

## 3. Try it

Here are a few things to try:

> Create a new tmux session called "workspace" with a window named "build".

> Send `make test` to the pane in my build window, then wait for it to finish and capture the output.

> Search all my panes for the word "error".

## Next steps

- {ref}`concepts` — Understand the tmux hierarchy and how tools target panes
- {ref}`configuration` — Environment variables and socket isolation
- {ref}`safety` — Control which tools are available
- {ref}`tools <tools-overview>` — Browse all available tools
