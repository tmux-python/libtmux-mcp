(prompting)=

# Agent prompting guide

How to write effective instructions for AI agents using libtmux-mcp.

## What the server tells your agent automatically

Every MCP client receives these instructions when connecting to the libtmux-mcp server. You do not need to repeat this information — the agent already knows it.

```{code-block} text
:class: server-prompt

libtmux MCP server for programmatic tmux control. tmux hierarchy:
Server > Session > Window > Pane. Use pane_id (e.g. '%1') as the
preferred targeting method - it is globally unique within a tmux server.
Use run_command for authored shell commands, send_keys or
send_keys_batch for raw TUI / persistent-shell input, capture_pane for
one-shot reads, and capture_since for repeated observation. Targeted tools
accept an optional socket_name parameter for multi-server support. Target
precedence is: explicit per-call selector, configured path, configured name,
frozen caller socket, tmux default.

IMPORTANT — metadata vs content: list_windows, list_panes, and
list_sessions only search metadata (names, IDs, current command). To
find text that is actually visible in terminals — when users ask what
panes 'contain', 'mention', 'show', or 'have' — use search_panes to
search across all pane contents, capture_since for repeated reads of a
known pane, or capture_pane for a one-shot manual inspection.
```

The server also dynamically adds:
- **Safety tier context**: Which tier is active and what tools are available
- **Caller pane awareness**: {tooliconl}`where-am-i` resolves "this pane",
  "current window", and "this session" without list-and-filter discovery. Its
  typed result keeps caller identity separate from whether the pane is still
  available on the configured server. See {ref}`concepts` "Agent
  self-awareness" for details.

## Activation and discovery

This server is designed to fire on bare terminal-surface phrasing — *"split this pane"*, *"what's in my current window"*, *"start a session for the build"* — without you having to say "tmux". The server treats `pane`, `session`, `window`, and `split` as positive triggers; tmux ID prefixes (`%1`, `@1`, `$1`) are unambiguous.

The server stays out of the way when you mean a browser window, an editor split, an i3/Hyprland workspace, or a Jupyter notebook cell — if your phrasing is ambiguous, the agent will ask before acting.

### Always-on tool listing (Claude Code only)

[Claude Code](https://code.claude.com/docs/en/mcp) defers loading MCP tool schemas when they exceed ~10% of your context window. By default, the three discovery anchors ({toolref}`where-am-i`, {toolref}`list-windows`, {toolref}`snapshot-pane`) carry an `anthropic/alwaysLoad: true` hint so bare *"pane"* / *"window"* prompts surface this server without a `ToolSearch` hop.

To force libtmux-mcp's *full* schema list to load upfront — useful if you also want {toolref}`send-keys`, {toolref}`capture-pane`, {toolref}`select-window` etc. preloaded — add `"alwaysLoad": true` at the server entry level (requires Claude Code v2.1.121+):

```json
{
  "mcpServers": {
    "tmux": {
      "command": "uvx",
      "args": ["libtmux-mcp"],
      "alwaysLoad": true
    }
  }
}
```

Cost: ~3-5K tokens of permanent context budget. Use only if libtmux-mcp is one of your top-3 most-used MCPs.

## Effective prompt patterns

These natural-language prompts reliably trigger the right tool sequences:

| Prompt | Agent interprets as |
|--------|-------------------|
| [What's running in this window?]{.prompt} | {toolref}`where-am-i` → {toolref}`list-panes` for the returned `window_id` |
| [Run `pytest` in my build pane and show results]{.prompt} | {toolref}`run-command` |
| [Start the dev server and wait until it's ready]{.prompt} | {toolref}`send-keys` → {toolref}`wait-for-text` (for "listening on" — third-party output the agent doesn't author) |
| [Spin up the dev server in the bottom-right pane]{.prompt} | {toolref}`find-pane-by-position` (corner=bottom-right) → {toolref}`send-keys` → {toolref}`wait-for-text` (for the server's readiness banner) |
| [Check if any pane has errors]{.prompt} | {toolref}`search-panes` with pattern "error" |
| [Keep watching the server pane]{.prompt} | {toolref}`capture-since` with the previous cursor |
| [Set up a workspace with editor, server, and tests]{.prompt} | {toolref}`create-session` → {toolref}`split-window` (x2) → {toolref}`set-pane-title` (x3) |
| [What's running in my tmux sessions?]{.prompt} | {toolref}`list-sessions` → {toolref}`list-panes` → {toolref}`capture-pane` |
| [Kill the old workspace session]{.prompt} | {toolref}`kill-session` (after confirming target) |

## Anti-patterns to avoid

| Prompt | Problem | Better version |
|--------|---------|---------------|
| [Run this command]{.prompt} | Ambiguous — agent may use its own shell instead of tmux | [Run `make test` in a tmux pane]{.prompt} |
| [Check my terminal]{.prompt} | Which pane? Agent must discover first | [Check the pane running `npm dev`]{.prompt} or [Search all panes for errors]{.prompt} |
| [Clean up everything]{.prompt} | Too broad for destructive operations | [Kill the `ci-test` session]{.prompt} |
| [Show me the output]{.prompt} | Capture immediately? Or wait? | [Wait for the command to finish, then show me the output]{.prompt} |

## System prompt fragments

Copy these into your agent's system instructions (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, or MCP client config) to improve behavior:

### For general tmux workflows

```{code-block} text
:class: system-prompt

When executing long-running commands (servers, builds, test suites),
use tmux via the libtmux MCP server rather than running them directly.
This keeps output accessible for later inspection.

For authored shell commands that need status, use run_command. For
raw TUI input or persistent shell state, use send_keys or
send_keys_batch. For custom command completion outside run_command,
compose `tmux wait-for -S <channel>` into the shell command and call
wait_for_channel — deterministic, no polling. Use wait_for_text or
wait_for_content_change for observation flows (third-party logs,
daemon prompts), and use capture_since when you need to read the same
pane repeatedly. Never capture_pane immediately after send_keys — the
command may still be running.
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

### To bias activation on bare "pane" / "window" / "session"

```{code-block} text
:class: system-prompt

This project uses tmux for its development workflow. When the user
says "pane", "window", or "session" without further qualification,
prefer the tmux MCP tools (libtmux-mcp) before assuming GUI semantics.
```

This is the lever closest to the failure mode for *"current window"* / *"this session"* anaphora — a project-level instructions file (`AGENTS.md`, `CLAUDE.md`, or whichever your host honors) lives one prompt-layer above the MCP server's `instructions` and your host composes it into the system prompt with higher priority.

## Tool selection heuristics

When an agent is unsure which tool to use, these rules help:

1. **Resolve relationships first**: Call {toolref}`where-am-i` for "this pane", "current window", or "this session". Use {toolref}`list-sessions` or {toolref}`list-panes` when you need an inventory instead.
2. **Prefer IDs**: Once you have a `pane_id`, use it for all subsequent calls — it never changes during the pane's lifetime
3. **Run, wait, or observe deliberately**: For commands the agent authors, prefer {toolref}`run-command`. Use {toolref}`wait-for-channel` only for custom shell composition outside that shape. Use {toolref}`capture-since` for repeated observation, and fall back to {toolref}`wait-for-text` or {toolref}`wait-for-content-change` for output the agent doesn't author. Never call {toolref}`capture-pane` in a retry loop.
4. **Content vs. metadata**: If looking for text *in* a terminal, use {toolref}`search-panes`. If looking for pane *properties* (name, PID, path), use {toolref}`list-panes` or {toolref}`get-pane-info`
5. **Destructive tools are opt-in**: Never kill sessions, windows, or panes unless the user explicitly asks
