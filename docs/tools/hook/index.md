# Hook tools

tmux hooks attach commands to lifecycle events — `pane-exited`,
`session-renamed`, `command-error`, and so on. libtmux-mcp exposes hook
inspection, but no dedicated tool that installs or removes one.

## Why no `set_hook`?

The hook API is deliberately inspection-only. tmux servers outlive the MCP
process and can run tmux command lists. Cleanup is not guaranteed after
SIGKILL, OOM, or a native crash, so this server exposes no dedicated
hook-write tool.
Keep intentional persistent hooks in tmux configuration.

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
