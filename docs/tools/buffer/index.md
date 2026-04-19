# Buffer tools

tmux paste buffers are a server-global namespace shared by every client on the same socket. The buffer tools in libtmux-mcp expose a narrow, agent-namespaced subset: every allocation gets a UUID-scoped name like `libtmux_mcp_<32-hex>_<logical>`, so concurrent agents (or parallel tool calls from one agent) cannot collide on each other's payloads.

There is **no** `list_buffers` tool. The user's OS clipboard often syncs into tmux paste buffers, so a generic enumeration would leak passwords, tokens, and other private content the agent has no business reading. Callers track the buffers they own via the {tool}`load-buffer` returns.

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
