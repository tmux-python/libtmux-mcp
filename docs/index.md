(index)=

# libtmux-mcp

Terminal control for AI agents, built on [libtmux](https://libtmux.git-pull.com) and [FastMCP](https://gofastmcp.com).

This server maps tmux's object hierarchy — sessions, windows, panes — into MCP tools. Some tools read state. Some mutate it. Some destroy. The distinction is explicit and enforced.

```{warning}
**Pre-alpha.** APIs may change. [Feedback welcome](https://github.com/tmux-python/libtmux-mcp/issues).
```

```{mcp-install}
:variant: compact
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

:::{grid-item-card} Prompts
:link: prompts
:link-type: doc

Four workflow recipes the client renders for the model.
:::

:::{grid-item-card} Resources
:link: resources
:link-type: doc

Snapshot views of the tmux hierarchy via `tmux://` URIs.
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

{toolref}`list-sessions` · {toolref}`capture-pane` · {toolref}`capture-since` · {toolref}`snapshot-pane` · {toolref}`get-pane-info` · {toolref}`find-pane-by-position` · {toolref}`search-panes` · {toolref}`wait-for-text` · {toolref}`wait-for-content-change` · {toolref}`display-message` · {toolref}`call-readonly-tools-batch`

### Act (mutating)

Create or modify tmux objects.

{toolref}`create-session` · {toolref}`send-keys` · {toolref}`send-keys-batch` · {toolref}`run-command` · {toolref}`paste-text` · {toolref}`create-window` · {toolref}`split-window` · {toolref}`select-pane` · {toolref}`select-window` · {toolref}`move-window` · {toolref}`resize-pane` · {toolref}`pipe-pane` · {toolref}`set-option` · {toolref}`call-mutating-tools-batch`

### Destroy (destructive)

Tear down tmux objects. Not reversible.

{toolref}`kill-session` · {toolref}`kill-window` · {toolref}`kill-pane` · {toolref}`kill-server` · {toolref}`call-destructive-tools-batch`

### Example: keep test runs out of persistent history

libtmux-mcp provides best-effort history suppression for Bash, Zsh, and Fish.
MCP calls to {tooliconl}`run-command` use lightweight command suppression by
default. When you create a new shell, opt into stronger no-disk controls with
`suppress_persistent_history=true`.

```{admonition} Prompt
:class: prompt

Create a tmux session called "checks" with best-effort no-disk shell-history
controls, run `pytest -q` in its initial pane, and show me the result.
```

The agent calls {tooliconl}`create-session` with
`suppress_persistent_history=true`, reuses `active_pane_id` from the returned
{class}`~libtmux_mcp.models.SessionInfo`, and calls
{tooliconl}`run-command` without an override. The same spawn option on
{toolref}`create-window`, {toolref}`split-window`, or
{toolref}`respawn-pane` applies only to the process that call starts.

These controls reduce history noise; they do not make commands secret. See
{ref}`history-suppression` for shell-specific behavior,
{ref}`configuration` for the server default, and {ref}`safety` for other
observation surfaces.

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
prompts
resources
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
migration
GitHub <https://github.com/tmux-python/libtmux-mcp>
```
