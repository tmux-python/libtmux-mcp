# Hooks

tmux hooks let you attach commands to lifecycle events — `pane-exited`,
`session-renamed`, `command-error`, and so on. libtmux-mcp exposes
**read-only** hook introspection so agents can audit what hooks the
human user has configured before running automation that might trigger
them.

## Why no `set_hook`?

Write-hooks are deliberately not exposed. tmux servers outlive the
MCP process, and FastMCP's `lifespan` teardown runs only on graceful
SIGTERM/SIGINT — it's bypassed on `kill -9`, OOM-kill, and
C-extension-fault crashes. Any cleanup registry in Python could be
silently bypassed, leaking agent-installed shell hooks into the
user's persistent tmux server where they would fire forever. Three
plausible future paths exist (a tmux-side `client-detached`
meta-hook for self-cleanup, requiring `LIBTMUX_SAFETY=destructive`,
or exposing one-shot `run_hook` only); none is in scope.

Until one of those paths is implemented, the surface here is
visibility only.

## Inspect

```{fastmcp-tool} hook_tools.show_hooks
```

**Use when** you need to enumerate every hook configured on a
target — the human user's tmux config, an inherited team setup, or
a session that another tool may have touched.

**Side effects:** None. Readonly.

```{fastmcp-tool-input} hook_tools.show_hooks
```

---

```{fastmcp-tool} hook_tools.show_hook
```

**Use when** you know which hook you want to inspect by name. Returns
empty when the hook is unset; raises `ToolError` for unknown hook
names (typos, wrong scope) so input mistakes don't masquerade as
"nothing configured".

**Side effects:** None. Readonly.

```{fastmcp-tool-input} hook_tools.show_hook
```
