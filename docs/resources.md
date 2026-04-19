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
