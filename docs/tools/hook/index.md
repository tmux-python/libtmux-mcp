# Hook tools

tmux hooks let you attach commands to lifecycle events — `pane-exited`, `session-renamed`, `command-error`, and so on. libtmux-mcp exposes **read-only** hook introspection so agents can audit what hooks the human user has configured before running automation that might trigger them.

## Why no `set_hook`?

Write-hooks are deliberately not exposed. tmux servers outlive the MCP process, and FastMCP's `lifespan` teardown runs only on graceful SIGTERM/SIGINT — it's bypassed on `kill -9`, OOM-kill, and C-extension-fault crashes. Any cleanup registry in Python could be silently bypassed, leaking agent-installed shell hooks into the user's persistent tmux server where they would fire forever. Three plausible future paths exist (a tmux-side `client-detached` meta-hook for self-cleanup, requiring `LIBTMUX_SAFETY=destructive`, or exposing one-shot `run_hook` only); none is in scope.

Until one of those paths is implemented, the surface here is visibility only.

## Inspect

::::{grid} 1 2 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`show-hooks`
Enumerate bindings at a scope.
:::

:::{grid-item-card} {tooliconl}`show-hook`
Inspect a single binding.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

show-hooks
show-hook
```
