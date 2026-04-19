# Buffer tools

Paste-buffer tools — stage, push, inspect, and delete MCP-namespaced tmux buffers for multi-line input.

::::{grid} 1 2 2 3
:gutter: 2 2 3 3

:::{grid-item-card} load_buffer
:link: load-buffer
:link-type: ref
Stage content into a new tmux paste buffer.
:::

:::{grid-item-card} paste_buffer
:link: paste-buffer
:link-type: ref
Push a staged buffer into a pane.
:::

:::{grid-item-card} show_buffer
:link: show-buffer
:link-type: ref
Read a staged buffer's contents back.
:::

:::{grid-item-card} delete_buffer
:link: delete-buffer
:link-type: ref
Free the server-side state of a staged buffer.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

load-buffer
paste-buffer
show-buffer
delete-buffer
```
