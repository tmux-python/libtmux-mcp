(resources-overview)=

# Resources

MCP resources are addressable documents clients read via
``resources/read``. The tmux hierarchy is exposed at ``tmux://`` URIs as
[resource templates](https://modelcontextprotocol.io/specification/2026-07-28/server/resources#resource-templates)
— each URI includes a ``{?socket_name}`` query parameter for socket
selection, plus structural path parameters (``{session_name}``,
``{pane_id}``, …) so a single template covers every session, window,
or pane.

Every ``tmux://`` resource requests a snapshot of the tmux hierarchy. Its
handler accepts identifiers and socket names rather than command text, but it
still sends tmux queries. A target `command-alias` or hook may add effects; see
{ref}`trust`.

`LIBTMUX_TOOLSETS` filters tools only. Resources remain available when no
toolsets are enabled. They do not carry ToolAnnotations or produce a
`tools/call` audit event.

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

:::{grid-item-card} `pane_viewer`
:link: pane-viewer
:link-type: ref
MCP Apps page that shows a snapshot_pane result.
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

(pane-viewer)=

## Pane viewer

``ui://libtmux-mcp/pane-viewer.html`` is a fixed page, not a template.
{tooliconl}`snapshot-pane` links to it, so a host that renders
[MCP Apps](https://modelcontextprotocol.io/docs/extensions/apps) — Claude
Desktop or VS Code — shows the snapshot as a terminal screen with the cursor
marked. While the page is visible and its *live* box is checked, it calls
{tooliconl}`snapshot-pane` again every two seconds. Clients without the Apps
extension ignore the link and receive the ordinary result.

The page loads the MCP Apps SDK from `unpkg.com`, the one origin its content
security policy admits.

```{fastmcp-resource} pane_viewer
```
