# Server tools

Server & process-level tools — discover sessions, control the tmux daemon, read/write server-scoped state.

::::{grid} 1 2 2 3
:gutter: 2 2 3 3

:::{grid-item-card} list_sessions
:link: list-sessions
:link-type: ref
List all sessions on the tmux server.
:::

:::{grid-item-card} list_servers
:link: list-servers
:link-type: ref
Discover tmux daemons on the system.
:::

:::{grid-item-card} get_server_info
:link: get-server-info
:link-type: ref
Query server process identity and version.
:::

:::{grid-item-card} create_session
:link: create-session
:link-type: ref
Create a new tmux session.
:::

:::{grid-item-card} kill_server
:link: kill-server
:link-type: ref
Terminate the tmux daemon. Destructive.
:::

:::{grid-item-card} show_option
:link: show-option
:link-type: ref
Read a tmux option (server / session / window / pane scope).
:::

:::{grid-item-card} set_option
:link: set-option
:link-type: ref
Set a tmux option.
:::

:::{grid-item-card} show_environment
:link: show-environment
:link-type: ref
Read the server's environment variables.
:::

:::{grid-item-card} set_environment
:link: set-environment
:link-type: ref
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
