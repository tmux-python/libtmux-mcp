(tools-overview)=

# Tools

All tools accept an optional `socket_name` parameter for multi-server support. It defaults to the `LIBTMUX_SOCKET` env var. See {ref}`configuration`.

## Inspect

Read tmux state without changing anything.

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} list_sessions
:link: sessions
:link-type: doc
List all active sessions.
:::

:::{grid-item-card} list_windows
:link: windows
:link-type: doc
List windows in a session.
:::

:::{grid-item-card} list_panes
:link: windows
:link-type: doc
List panes in a window.
:::

:::{grid-item-card} capture_pane
:link: panes
:link-type: doc
Read visible content of a pane.
:::

:::{grid-item-card} get_pane_info
:link: panes
:link-type: doc
Get detailed pane metadata.
:::

:::{grid-item-card} search_panes
:link: panes
:link-type: doc
Search text across panes.
:::

:::{grid-item-card} wait_for_text
:link: panes
:link-type: doc
Wait for text to appear in a pane.
:::

:::{grid-item-card} get_server_info
:link: sessions
:link-type: doc
Get tmux server info.
:::

:::{grid-item-card} show_option
:link: options
:link-type: doc
Query a tmux option value.
:::

:::{grid-item-card} show_environment
:link: options
:link-type: doc
Show tmux environment variables.
:::

::::

## Act

Create or modify tmux objects.

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} create_session
:link: sessions
:link-type: doc
Start a new tmux session.
:::

:::{grid-item-card} create_window
:link: windows
:link-type: doc
Add a window to a session.
:::

:::{grid-item-card} split_window
:link: windows
:link-type: doc
Split a window into panes.
:::

:::{grid-item-card} send_keys
:link: panes
:link-type: doc
Send commands or keystrokes to a pane.
:::

:::{grid-item-card} rename_session
:link: sessions
:link-type: doc
Rename a session.
:::

:::{grid-item-card} rename_window
:link: windows
:link-type: doc
Rename a window.
:::

:::{grid-item-card} resize_pane
:link: panes
:link-type: doc
Adjust pane dimensions.
:::

:::{grid-item-card} resize_window
:link: windows
:link-type: doc
Adjust window dimensions.
:::

:::{grid-item-card} select_layout
:link: windows
:link-type: doc
Set window layout.
:::

:::{grid-item-card} set_pane_title
:link: panes
:link-type: doc
Set pane title.
:::

:::{grid-item-card} clear_pane
:link: panes
:link-type: doc
Clear pane content.
:::

:::{grid-item-card} set_option
:link: options
:link-type: doc
Set a tmux option.
:::

:::{grid-item-card} set_environment
:link: options
:link-type: doc
Set a tmux environment variable.
:::

::::

## Destroy

Tear down tmux objects. Not reversible.

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} kill_session
:link: sessions
:link-type: doc
Destroy a session and all its windows.
:::

:::{grid-item-card} kill_window
:link: windows
:link-type: doc
Destroy a window and all its panes.
:::

:::{grid-item-card} kill_pane
:link: panes
:link-type: doc
Destroy a pane.
:::

:::{grid-item-card} kill_server
:link: sessions
:link-type: doc
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
