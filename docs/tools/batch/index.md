# Batch tools

Batch tools coordinate existing MCP tool calls. They do not replace tmux
targeting: each nested tool call still supplies its own arguments,
including `socket_name` when needed.

::::{grid} 1 1 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`call-read-tools-batch`
Call several `inspect` tools in order.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

call-read-tools-batch
```
