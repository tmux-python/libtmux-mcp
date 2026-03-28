(tools-overview)=

# Tools

All tools accept an optional `socket_name` parameter for multi-server support. It defaults to the {envvar}`LIBTMUX_SOCKET` env var. See {ref}`configuration`.

## Which tool do I want?

**Reading terminal content?**
- Know which pane? → {tool}`capture-pane`
- Don't know which pane? → {tool}`search-panes`
- Need to wait for output? → {tool}`wait-for-text`
- Only need metadata (PID, path, size)? → {tool}`get-pane-info`

**Running a command?**
- {tool}`send-keys` — then {tool}`wait-for-text` + {tool}`capture-pane`

**Creating workspace structure?**
- New session → {tool}`create-session`
- New window → {tool}`create-window`
- New pane → {tool}`split-window`

**Changing settings?**
- tmux options → {tool}`show-option` / {tool}`set-option`
- Environment vars → {tool}`show-environment` / {tool}`set-environment`

## Inspect

Read tmux state without changing anything.

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} list_sessions
:link: list-sessions
:link-type: ref
List all active sessions.
:::

:::{grid-item-card} list_windows
:link: list-windows
:link-type: ref
List windows in a session.
:::

:::{grid-item-card} list_panes
:link: list-panes
:link-type: ref
List panes in a window.
:::

:::{grid-item-card} capture_pane
:link: capture-pane
:link-type: ref
Read visible content of a pane.
:::

:::{grid-item-card} get_pane_info
:link: get-pane-info
:link-type: ref
Get detailed pane metadata.
:::

:::{grid-item-card} search_panes
:link: search-panes
:link-type: ref
Search text across panes.
:::

:::{grid-item-card} wait_for_text
:link: wait-for-text
:link-type: ref
Wait for text to appear in a pane.
:::

:::{grid-item-card} get_server_info
:link: get-server-info
:link-type: ref
Get tmux server info.
:::

:::{grid-item-card} show_option
:link: show-option
:link-type: ref
Query a tmux option value.
:::

:::{grid-item-card} show_environment
:link: show-environment
:link-type: ref
Show tmux environment variables.
:::

::::

## Act

Create or modify tmux objects.

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} create_session
:link: create-session
:link-type: ref
Start a new tmux session.
:::

:::{grid-item-card} create_window
:link: create-window
:link-type: ref
Add a window to a session.
:::

:::{grid-item-card} split_window
:link: split-window
:link-type: ref
Split a window into panes.
:::

:::{grid-item-card} send_keys
:link: send-keys
:link-type: ref
Send commands or keystrokes to a pane.
:::

:::{grid-item-card} rename_session
:link: rename-session
:link-type: ref
Rename a session.
:::

:::{grid-item-card} rename_window
:link: rename-window
:link-type: ref
Rename a window.
:::

:::{grid-item-card} resize_pane
:link: resize-pane
:link-type: ref
Adjust pane dimensions.
:::

:::{grid-item-card} resize_window
:link: resize-window
:link-type: ref
Adjust window dimensions.
:::

:::{grid-item-card} select_layout
:link: select-layout
:link-type: ref
Set window layout.
:::

:::{grid-item-card} set_pane_title
:link: set-pane-title
:link-type: ref
Set pane title.
:::

:::{grid-item-card} clear_pane
:link: clear-pane
:link-type: ref
Clear pane content.
:::

:::{grid-item-card} set_option
:link: set-option
:link-type: ref
Set a tmux option.
:::

:::{grid-item-card} set_environment
:link: set-environment
:link-type: ref
Set a tmux environment variable.
:::

::::

## Destroy

Tear down tmux objects. Not reversible.

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} kill_session
:link: kill-session
:link-type: ref
Destroy a session and all its windows.
:::

:::{grid-item-card} kill_window
:link: kill-window
:link-type: ref
Destroy a window and all its panes.
:::

:::{grid-item-card} kill_pane
:link: kill-pane
:link-type: ref
Destroy a pane.
:::

:::{grid-item-card} kill_server
:link: kill-server
:link-type: ref
Kill the entire tmux server.
:::

::::

```{toctree}
:hidden:

sessions
windows
panes
options
```
