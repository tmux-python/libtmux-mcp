# Server tools

Server & process-level tools — discover sessions, control the tmux daemon, read/write server-scoped state.

::::{grid} 1 2 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`list-sessions`
List all sessions on the tmux server.
:::

:::{grid-item-card} {tooliconl}`list-servers`
Discover tmux daemons on the system.
:::

:::{grid-item-card} {tooliconl}`get-server-info`
Query server process identity and version.
:::

:::{grid-item-card} {tooliconl}`create-session`
Create a new tmux session.
:::

:::{grid-item-card} {tooliconl}`kill-server`
Terminate the tmux daemon. Destructive.
:::

:::{grid-item-card} {tooliconl}`show-option`
Read a tmux option (server / session / window / pane scope).
:::

:::{grid-item-card} {tooliconl}`set-option`
Set a tmux option.
:::

:::{grid-item-card} {tooliconl}`show-environment`
Read the server's environment variables.
:::

:::{grid-item-card} {tooliconl}`set-environment`
Set an environment variable on the server.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

list-sessions
list-servers
get-server-info
create-session
kill-server
show-option
set-option
show-environment
set-environment
```
