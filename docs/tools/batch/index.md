# Batch tools

Batch tools coordinate existing MCP tool calls. They do not replace tmux
targeting: each nested tool call still supplies its own arguments,
including `socket_name` when needed.

::::{grid} 1 1 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`call-readonly-tools-batch`
Call readonly tools in order.
:::

:::{grid-item-card} {tooliconl}`call-mutating-tools-batch`
Call readonly or mutating tools in order.
:::

:::{grid-item-card} {tooliconl}`call-destructive-tools-batch`
Call readonly, mutating, or destructive tools in order.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

call-readonly-tools-batch
call-mutating-tools-batch
call-destructive-tools-batch
```
