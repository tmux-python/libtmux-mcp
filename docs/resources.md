(resources-overview)=

# Resources

MCP resources are addressable documents the server exposes at
``tmux://`` URIs. Clients read them via ``resources/read``. All
libtmux-mcp resources are
[resource templates](https://modelcontextprotocol.io/specification/2025-11-25/server/resources#resource-templates)
— each URI includes a ``{?socket_name}`` query parameter for socket
isolation, plus structural path parameters (``{session_name}``,
``{pane_id}``, …) so a single template covers every session, window,
or pane.

Every resource delivers a snapshot of the tmux hierarchy at call
time. Agents use them for read-only inspection; any write workflow
goes through the corresponding {doc}`tools </tools/index>`.

## Available resources

::::{grid} 1 2 2 3
:gutter: 2 2 3 3

:::{grid-item-card} `get_sessions`
:link: fastmcp-resource-template-get-sessions
:link-type: ref
List all tmux sessions.
:::

:::{grid-item-card} `get_session`
:link: fastmcp-resource-template-get-session
:link-type: ref
Get details of a specific tmux session.
:::

:::{grid-item-card} `get_session_windows`
:link: fastmcp-resource-template-get-session-windows
:link-type: ref
List all windows in a tmux session.
:::

:::{grid-item-card} `get_window`
:link: fastmcp-resource-template-get-window
:link-type: ref
Get details of a specific window in a session.
:::

:::{grid-item-card} `get_pane`
:link: fastmcp-resource-template-get-pane
:link-type: ref
Get details of a specific pane.
:::

:::{grid-item-card} `get_pane_content`
:link: fastmcp-resource-template-get-pane-content
:link-type: ref
Capture and return the content of a pane.
:::

::::

---

## Sessions

```{fastmcp-resource-template} get_sessions
```

```{fastmcp-resource-template} get_session
```

```{fastmcp-resource-template} get_session_windows
```

## Windows

```{fastmcp-resource-template} get_window
```

## Panes

```{fastmcp-resource-template} get_pane
```

```{fastmcp-resource-template} get_pane_content
```
