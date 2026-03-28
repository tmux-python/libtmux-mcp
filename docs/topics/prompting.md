(prompting)=

# Agent prompting guide

How to write effective instructions for AI agents using libtmux-mcp.

## What the server tells your agent automatically

Every MCP client receives these instructions when connecting to the libtmux-mcp server. You do not need to repeat this information — the agent already knows it.

```text
libtmux MCP server for programmatic tmux control. tmux hierarchy:
Server > Session > Window > Pane. Use pane_id (e.g. '%1') as the
preferred targeting method - it is globally unique within a tmux server.
Use send_keys to execute commands and capture_pane to read output. All
tools accept an optional socket_name parameter for multi-server support
(defaults to LIBTMUX_SOCKET env var).

IMPORTANT — metadata vs content: list_windows, list_panes, and
list_sessions only search metadata (names, IDs, current command). To
find text that is actually visible in terminals — when users ask what
panes 'contain', 'mention', 'show', or 'have' — use search_panes to
search across all pane contents, or list_panes + capture_pane on each
pane for manual inspection.
```

The server also dynamically adds:
- **Safety tier context**: Which tier is active and what tools are available
- **Caller pane awareness**: If the server runs inside tmux, it tells the agent which pane is its own (via `TMUX_PANE`)

## Effective prompt patterns

These natural-language prompts reliably trigger the right tool sequences:

| Prompt | Agent interprets as |
|--------|-------------------|
| "Run `pytest` in my build pane and show results" | {toolref}`send-keys` → {toolref}`wait-for-text` → {toolref}`capture-pane` |
| "Start the dev server and wait until it's ready" | {toolref}`send-keys` → {toolref}`wait-for-text` (for "listening on") |
| "Check if any pane has errors" | {toolref}`search-panes` with pattern "error" |
| "Set up a workspace with editor, server, and tests" | {toolref}`create-session` → {toolref}`split-window` (x2) → {toolref}`set-pane-title` (x3) |
| "What's running in my tmux sessions?" | {toolref}`list-sessions` → {toolref}`list-panes` → {toolref}`capture-pane` |
| "Kill the old workspace session" | {toolref}`kill-session` (after confirming target) |

## Anti-patterns to avoid

| Prompt | Problem | Better version |
|--------|---------|---------------|
| "Run this command" | Ambiguous — agent may use its own shell instead of tmux | "Run `make test` in a tmux pane" |
| "Check my terminal" | Which pane? Agent must discover first | "Check the pane running `npm dev`" or "Search all panes for errors" |
| "Clean up everything" | Too broad for destructive operations | "Kill the `ci-test` session" |
| "Show me the output" | Capture immediately? Or wait? | "Wait for the command to finish, then show me the output" |

## System prompt fragments

Copy these into your agent's system instructions (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, or MCP client config) to improve behavior:

### For general tmux workflows

```{code-block} text
:class: system-prompt

When executing long-running commands (servers, builds, test suites),
use tmux via the libtmux MCP server rather than running them directly.
This keeps output accessible for later inspection. Use the pattern:
send_keys → wait_for_text (for completion signal) → capture_pane.
```

### For safe agent behavior

```{code-block} text
:class: system-prompt

Before creating tmux sessions, check list_sessions to avoid duplicates.
Always use pane_id for targeting — it is globally unique. Never run
destructive operations (kill_session, kill_server) without confirming
the target with the user first.
```

### For development workflows

```{code-block} text
:class: system-prompt

When the user asks you to run tests or start servers, use dedicated
tmux panes. Split windows to run related processes side-by-side.
Use wait_for_text to know when a server is ready before running tests
that depend on it.
```

## Tool selection heuristics

When an agent is unsure which tool to use, these rules help:

1. **Discovery first**: Call {toolref}`list-sessions` or {toolref}`list-panes` before acting on specific targets
2. **Prefer IDs**: Once you have a `pane_id`, use it for all subsequent calls — it never changes during the pane's lifetime
3. **Wait, don't poll**: Use {toolref}`wait-for-text` instead of repeatedly calling {toolref}`capture-pane` in a loop
4. **Content vs. metadata**: If looking for text *in* a terminal, use {toolref}`search-panes`. If looking for pane *properties* (name, PID, path), use {toolref}`list-panes` or {toolref}`get-pane-info`
5. **Destructive tools are opt-in**: Never kill sessions, windows, or panes unless the user explicitly asks
