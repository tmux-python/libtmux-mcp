# Chain tools

Chain tools compile typed tmux operations into the fewest safe native
tmux dispatches. They are different from batch tools: batch tools call
existing MCP tools one by one, while chain tools lower a typed operation
list directly to tmux command sequences when tmux can preserve the same
semantics.

::::{grid} 1 1 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`run-tmux-operations`
Run typed tmux operations with automatic native chaining.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

run-tmux-operations
```
