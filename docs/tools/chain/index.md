# Chain tools

Chain tools run a typed list of tmux operations over a persistent tmux
control connection, one dispatch per operation, and return one typed
result per step. They are different from batch tools: batch tools call
existing MCP tools one by one, while chain tools take a typed tmux
operation list directly.

::::{grid} 1 1 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`run-tmux-plan`
Run a typed plan of tmux operations, one result per step.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

run-tmux-plan
```
