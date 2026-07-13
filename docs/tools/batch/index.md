# Batch tools

Batch tools coordinate existing MCP tool calls. They do not replace tmux
targeting: each nested tool call still supplies its own arguments,
including `socket_name` when needed.

::::{grid} 1 1 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`call-tools-batch`
Call existing MCP tools in order, with an optional safety-tier cap.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

call-tools-batch
```
