(tools-overview)=

# Tools

All tools accept an optional `socket_name` parameter for multi-server support. It defaults to the `LIBTMUX_SOCKET` env var. See {ref}`configuration`.

::::{grid} 1 1 2 2
:gutter: 2 2 3 3

:::{grid-item-card} Discovery
Find and inspect tmux objects.
^^^
`list_sessions` `list_windows` `list_panes` `get_server_info` `get_pane_info`
:::

:::{grid-item-card} Capture & Search
Read and search terminal output.
^^^
`capture_pane` `search_panes` `wait_for_text`
:::

:::{grid-item-card} Session Lifecycle
Create and manage sessions.
^^^
`create_session` `rename_session` `kill_session`
:::

:::{grid-item-card} Windows & Panes
Create, split, and organize.
^^^
`create_window` `split_window` `rename_window` `select_layout` `resize_window` `resize_pane` `kill_window` `kill_pane`
:::

:::{grid-item-card} Execution
Send commands and interact with terminals.
^^^
`send_keys` `set_pane_title` `clear_pane`
:::

:::{grid-item-card} Options & Environment
Read and set tmux configuration.
^^^
`show_option` `set_option` `show_environment` `set_environment`
:::

:::{grid-item-card} Server Management
Destructive server operations.
^^^
`kill_server`
:::

::::

## Discovery

Find and inspect tmux objects.

- **`list_sessions`** — List all sessions (with optional filters)
- **`list_windows`** — List windows in a session or across all sessions
- **`list_panes`** — List panes in a window or across all windows
- **`get_server_info`** — Server status: version, socket path, session count, alive status
- **`get_pane_info`** — Pane metadata: size, title, current command, PID

## Capture and search

Read and search terminal output.

- **`capture_pane`** — Capture pane content as text (visible area or scrollback)
- **`search_panes`** — Search across all pane contents for text or regex
- **`wait_for_text`** — Wait for text to appear in a pane (polling with timeout)

## Session lifecycle

Create and manage sessions.

- **`create_session`** — Create a new session with optional window name, size, and env vars
- **`rename_session`** — Rename an existing session
- **`kill_session`** — Kill a session (destructive)

## Windows and panes

Create, split, and organize.

- **`create_window`** — Create a new window in a session
- **`split_window`** — Split a window to create a new pane (horizontal or vertical)
- **`rename_window`** — Rename a window
- **`select_layout`** — Set layout: `even-horizontal`, `even-vertical`, `main-horizontal`, `main-vertical`, `tiled`
- **`resize_window`** — Resize a window (width and/or height)
- **`resize_pane`** — Resize a pane (width, height, or zoom toggle)
- **`kill_window`** — Kill a window (destructive)
- **`kill_pane`** — Kill a pane (destructive)

## Execution

Send commands and interact with terminals.

- **`send_keys`** — Send keys or text to a pane (with optional Enter, literal mode, history suppression)
- **`set_pane_title`** — Set a pane's title
- **`clear_pane`** — Clear pane content and scrollback history

## Options and environment

Read and set tmux configuration.

- **`show_option`** — Query a tmux option value (server, session, window, or pane scope)
- **`set_option`** — Set a tmux option
- **`show_environment`** — Show tmux environment variables
- **`set_environment`** — Set a tmux environment variable

## Server management

- **`kill_server`** — Kill the tmux server (destructive)

## Tool parameter reference

For full parameter documentation (types, defaults, descriptions), see the
[API reference](../reference/api/index.md).
