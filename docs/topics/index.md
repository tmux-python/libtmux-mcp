# Topics

Explore libtmux-mcp's core ideas and design at a high level.

::::{grid} 1 1 2 2
:gutter: 2 2 3 3

:::{grid-item-card} Architecture
:link: architecture
:link-type: doc
Source layout, request flow, and extension points.
:::

:::{grid-item-card} Concepts
:link: concepts
:link-type: doc
tmux hierarchy, MCP protocol, and the mental model.
:::

:::{grid-item-card} Safety Tiers
:link: safety
:link-type: doc
Three-tier safety system for controlling tool access.
:::

:::{grid-item-card} Troubleshooting
:link: troubleshooting
:link-type: doc
Symptom-based guide for common issues.
:::

:::{grid-item-card} Gotchas
:link: gotchas
:link-type: doc
Things that will bite you if you don't know about them.
:::

:::{grid-item-card} Agent Prompting
:link: prompting
:link-type: doc
Write effective instructions for AI agents using tmux tools.
:::

::::

## MCP protocol utilities

How libtmux-mcp maps to three optional utility capabilities from
the Model Context Protocol specification.

::::{grid} 1 1 3 3
:gutter: 2 2 3 3

:::{grid-item-card} Completion
:link: completion
:link-type: doc
Argument auto-complete — what FastMCP derives automatically and
what libtmux-mcp does not yet wire up.
:::

:::{grid-item-card} Logging
:link: logging
:link-type: doc
Server-to-client log forwarding and the ``libtmux_mcp.*`` logger
hierarchy.
:::

:::{grid-item-card} Pagination
:link: pagination
:link-type: doc
Protocol-level cursors vs tool-level ``offset`` / ``limit`` (as in
``search_panes``).
:::

::::

```{toctree}
:hidden:

architecture
concepts
safety
gotchas
prompting
completion
logging
pagination
troubleshooting
```
