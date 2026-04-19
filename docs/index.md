(index)=

# libtmux-mcp

Terminal control for AI agents, built on [libtmux](https://libtmux.git-pull.com) and [FastMCP](https://gofastmcp.com).

This server maps tmux's object hierarchy — sessions, windows, panes — into MCP tools. Some tools read state. Some mutate it. Some destroy. The distinction is explicit and enforced.

```{warning}
**Pre-alpha.** APIs may change. [Feedback welcome](https://github.com/tmux-python/libtmux-mcp/issues).
```

---

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} Quickstart
:link: quickstart
:link-type: doc

Install, connect, get a first result. Under 2 minutes.
:::

:::{grid-item-card} Tools
:link: tools/index
:link-type: doc

Every tool, grouped by intent and safety tier.
:::

:::{grid-item-card} Safety tiers
:link: topics/safety
:link-type: doc

Readonly, mutating, destructive. Know what changes state.
:::

:::{grid-item-card} Client setup
:link: clients
:link-type: doc

Config blocks for Claude Desktop, Claude Code, Cursor, and others.
:::

::::

---

## What you can do

### Inspect (readonly)

Read tmux state without changing anything.

{toolref}`list-sessions` · {toolref}`capture-pane` · {toolref}`snapshot-pane` · {toolref}`get-pane-info` · {toolref}`search-panes` · {toolref}`wait-for-text` · {toolref}`wait-for-content-change` · {toolref}`display-message`

### Act (mutating)

Create or modify tmux objects.

{toolref}`create-session` · {toolref}`send-keys` · {toolref}`paste-text` · {toolref}`create-window` · {toolref}`split-window` · {toolref}`select-pane` · {toolref}`select-window` · {toolref}`move-window` · {toolref}`resize-pane` · {toolref}`pipe-pane` · {toolref}`set-option`

### Destroy (destructive)

Tear down tmux objects. Not reversible.

{toolref}`kill-session` · {toolref}`kill-window` · {toolref}`kill-pane` · {toolref}`kill-server`

[Browse all tools →](tools/index)

---

## Mental model

- **Object hierarchy** — sessions contain windows, windows contain panes ({doc}`topics/concepts`)
- **Read vs. mutate** — some tools observe, some act, some destroy ({doc}`topics/safety`)
- **tmux is the source of truth** — the server reads from it and writes to it, never caches or abstracts

---

```{toctree}
:hidden:
:caption: Get started

quickstart
installation
clients
```

```{toctree}
:hidden:
:caption: Use it

tools/index
recipes
configuration
```

```{toctree}
:hidden:
:caption: Understand it

topics/index
```

```{toctree}
:hidden:
:caption: Reference

reference/api/index
reference/compatibility
glossary
```

```{toctree}
:hidden:
:caption: Project

project/index
history
GitHub <https://github.com/tmux-python/libtmux-mcp>
```
