---
orphan: true
---

# Badge & Role Demo

A showcase of the custom Sphinx roles and visual elements available in libtmux-mcp documentation.

## Safety badges

Standalone badges via `{badge}`:

- {badge}`readonly` — green, read-only operations
- {badge}`mutating` — amber, state-changing operations
- {badge}`destructive` — red, irreversible operations

## Tool references

### `{tool}` — code-linked with badge

{tool}`capture-pane` · {tool}`send-keys` · {tool}`search-panes` · {tool}`wait-for-text` · {tool}`kill-pane` · {tool}`create-session` · {tool}`split-window`

### `{toolref}` — code-linked, no badge

{toolref}`capture-pane` · {toolref}`send-keys` · {toolref}`search-panes` · {toolref}`wait-for-text` · {toolref}`kill-pane` · {toolref}`create-session` · {toolref}`split-window`

### `{tooliconl}` — icon left, outside code

{tooliconl}`capture-pane` · {tooliconl}`send-keys` · {tooliconl}`search-panes` · {tooliconl}`wait-for-text` · {tooliconl}`kill-pane` · {tooliconl}`create-session` · {tooliconl}`split-window`

### `{tooliconr}` — icon right, outside code

{tooliconr}`capture-pane` · {tooliconr}`send-keys` · {tooliconr}`search-panes` · {tooliconr}`wait-for-text` · {tooliconr}`kill-pane` · {tooliconr}`create-session` · {tooliconr}`split-window`

### `{tooliconil}` — icon inline-left, inside code

{tooliconil}`capture-pane` · {tooliconil}`send-keys` · {tooliconil}`search-panes` · {tooliconil}`wait-for-text` · {tooliconil}`kill-pane` · {tooliconil}`create-session` · {tooliconil}`split-window`

### `{tooliconir}` — icon inline-right, inside code

{tooliconir}`capture-pane` · {tooliconir}`send-keys` · {tooliconir}`search-panes` · {tooliconir}`wait-for-text` · {tooliconir}`kill-pane` · {tooliconir}`create-session` · {tooliconir}`split-window`

### `{ref}` — plain text link

{ref}`capture-pane` · {ref}`send-keys` · {ref}`search-panes` · {ref}`wait-for-text` · {ref}`kill-pane` · {ref}`create-session` · {ref}`split-window`

## Badges in context

### In a heading

These are the actual tool headings as they render on tool pages:

> `capture_pane` {badge}`readonly`

> `split_window` {badge}`mutating`

> `kill_session` {badge}`destructive`

### In a table

| Tool | Tier | Description |
|------|------|-------------|
| {toolref}`list-sessions` | {badge}`readonly` | List all sessions |
| {toolref}`send-keys` | {badge}`mutating` | Send commands to a pane |
| {toolref}`kill-pane` | {badge}`destructive` | Destroy a pane |

### In prose

Use {tooliconl}`search-panes` to find text across all panes. If you know which pane, use {tooliconl}`capture-pane` instead. After running a command with {tooliconl}`send-keys`, always {tooliconl}`wait-for-text` before capturing.

### Dense inline (toolref, no badges)

The fundamental pattern: {toolref}`send-keys` → {toolref}`wait-for-text` → {toolref}`capture-pane`. For discovery: {toolref}`list-sessions` → {toolref}`list-panes` → {toolref}`get-pane-info`.

## Environment variable references

{envvar}`LIBTMUX_SOCKET` · {envvar}`LIBTMUX_SAFETY` · {envvar}`LIBTMUX_SOCKET_PATH` · {envvar}`LIBTMUX_TMUX_BIN`

## Glossary terms

{term}`SIGINT` · {term}`SIGQUIT` · {term}`MCP` · {term}`Safety tier` · {term}`Pane` · {term}`Session`

## Admonitions

```{tip}
Use {tooliconl}`search-panes` before {tooliconl}`capture-pane` when you don't know which pane has the output you need.
```

```{warning}
Do not call {toolref}`capture-pane` immediately after {toolref}`send-keys` — there is a race condition. Use {toolref}`wait-for-text` between them.
```

```{note}
All tools accept an optional `socket_name` parameter for multi-server support.
```

## Badge anatomy

Each badge renders as:

```html
<span class="sd-badge sd-bg-success"
      role="note"
      aria-label="Safety tier: readonly">
  🔍 readonly
</span>
```

Features:
- **Emoji icon** — 🔍 readonly, ✏️ mutating, 💣 destructive (native system emoji, no filters)
- **Matte colors** — forest green, smoky amber, matte crimson with 1px border
- **Accessible** — `role="note"` + `aria-label` for screen readers
- **Non-selectable** — `user-select: none` so copying tool names skips badge text
- **Context-aware sizing** — slightly larger in headings, smaller inline
- **Sidebar compression** — badges collapse to colored dots in the right-side TOC
- **Heading flex** — `h2/h3/h4:has(.sd-badge)` centers badge against cap-height
