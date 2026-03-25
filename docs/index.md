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

[`list_sessions`](#list-sessions) · [`capture_pane`](#capture-pane) · [`get_pane_info`](#get-pane-info) · [`search_panes`](#search-panes) · [`wait_for_text`](#wait-for-text)

### Act (mutating)

Create or modify tmux objects.

[`create_session`](#create-session) · [`send_keys`](#send-keys) · [`create_window`](#create-window) · [`split_window`](#split-window) · [`resize_pane`](#resize-pane) · [`set_option`](#set-option)

### Destroy (destructive)

Tear down tmux objects. Not reversible.

[`kill_session`](#kill-session) · [`kill_window`](#kill-window) · [`kill_pane`](#kill-pane) · [`kill_server`](#kill-server)

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
```

```{toctree}
:hidden:
:caption: Project

project/index
history
```
