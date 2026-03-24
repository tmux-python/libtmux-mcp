(index)=

# libtmux-mcp

MCP server for tmux, powered by [libtmux](https://libtmux.git-pull.com/).

```{warning}
**Pre-alpha.** APIs may change. [Feedback welcome](https://github.com/tmux-python/libtmux-mcp/issues).
```

## Get started in one command

```console
$ claude mcp add libtmux -- uvx libtmux-mcp
```

Then ask your agent:

```
> List all tmux sessions and show the panes in the first one.
```

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} Quickstart
:link: quickstart
:link-type: doc
Zero to a working tool invocation in 5 minutes.
:::

:::{grid-item-card} Tools
:link: tools/index
:link-type: doc
30+ tools for sessions, windows, panes, capture, and more.
:::

:::{grid-item-card} Configuration
:link: configuration
:link-type: doc
Environment variables, safety tiers, socket selection.
:::

::::

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} MCP Clients
:link: clients
:link-type: doc
Copy-pasteable config for Claude Code, Cursor, VS Code, and more.
:::

:::{grid-item-card} Topics
:link: topics/index
:link-type: doc
Architecture, concepts, safety tiers, troubleshooting.
:::

:::{grid-item-card} Contributing
:link: project/index
:link-type: doc
Development setup, code style, release process.
:::

::::

## Install

```console
$ uvx libtmux-mcp
```

```console
$ pip install libtmux-mcp
```

See [Installation](installation.md) for all methods and options.

```{toctree}
:hidden:

installation
quickstart
clients
configuration
tools/index
topics/index
reference/api/index
reference/compatibility
project/index
history
glossary
```
