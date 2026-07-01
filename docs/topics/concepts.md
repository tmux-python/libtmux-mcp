(concepts)=

# Concepts

The mental model you need to use libtmux-mcp effectively.

## tmux hierarchy

tmux organizes terminals in a strict hierarchy:

```
Server (tmux server instance)
  └─ Session (named group of windows, e.g. "dev")
      └─ Window (tab within a session, e.g. "editor")
          └─ Pane (terminal split within a window)
```

libtmux-mcp mirrors this hierarchy. Every tool operates on one of these objects.

## Identifiers

Each tmux object has identifiers you can use to target it:

| Object | ID format | Name | Index |
|--------|----------|------|-------|
| Session | `$1`, `$2` | `"dev"`, `"build"` | — |
| Window | `@1`, `@2` | `"editor"`, `"tests"` | `0`, `1`, `2` |
| Pane | `%1`, `%2` | — | — |

**Pane IDs are globally unique** within a tmux server and are the preferred targeting method. When you know the pane ID, use it directly — no session or window context needed.

Session names and window names are human-readable but may not be unique. Window indexes are unique within a session.

## Targeting

Most tools accept multiple targeting parameters. The resolution order is:

1. **Direct ID** — `pane_id`, `window_id`, or `session_id` (fastest, unambiguous)
2. **Name lookup** — `session_name` + optional `window_index` (convenient but may be ambiguous)
3. **Default** — If no targeting parameter is given, tools that need a single object will fail; list tools return everything

For pane tools, you can combine parameters to narrow the search: `session_name` + `window_id` → find the pane in that specific window.

## Discovery vs. mutation

Tools fall into three categories:

- **Discovery** — Read-only operations: {toolref}`list-sessions`,
  {toolref}`list-windows`, {toolref}`list-panes`,
  {toolref}`capture-pane`, {toolref}`capture-since`,
  {toolref}`get-pane-info`, {toolref}`find-pane-by-position`,
  {toolref}`search-panes`, {toolref}`wait-for-text`,
  {toolref}`show-option`, {toolref}`show-environment`
- **Mutation** — Create, modify, or send input:
  {toolref}`create-session`, {toolref}`create-window`,
  {toolref}`split-window`, {toolref}`send-keys`,
  {toolref}`send-keys-batch`, {toolref}`rename-session`,
  {toolref}`rename-window`, {toolref}`resize-pane`,
  {toolref}`resize-window`, {toolref}`set-pane-title`,
  {toolref}`clear-pane`, {toolref}`select-layout`,
  {toolref}`set-option`, {toolref}`set-environment`
- **Destruction** — Remove tmux objects: {toolref}`kill-server`,
  {toolref}`kill-session`, {toolref}`kill-window`, {toolref}`kill-pane`

These map to {ref}`safety tiers <safety>`.

## Agent self-awareness

When the MCP server runs inside a tmux pane (detected via the `TMUX_PANE` environment variable), it:

- Includes the caller's pane context in server instructions
- Annotates the caller's own pane with `is_caller=true` in tool results
- Prevents destructive tools from killing the caller's own pane, window, session, or server

This means agents can safely explore and manage tmux without accidentally terminating themselves.

## Server caching

Server instances are cached by `(socket_name, socket_path, tmux_bin)` tuple with thread-safe access. Dead servers are automatically evicted via {meth}`libtmux.Server.is_alive` checks. This means multiple tool calls reuse the same server connection efficiently.

## Filtering

List tools ({toolref}`list-sessions`, {toolref}`list-windows`, and
{toolref}`list-panes`) support Django-style filters:

```json
{"session_name__contains": "dev"}
```

Supported operators: `exact`, `contains`, `startswith`, `endswith`, `regex`, `icontains`, `iexact`, `istartswith`, `iendswith`, `iregex`.

## Resources

In addition to tools, the MCP server exposes `tmux://` URI resources for browsing the hierarchy:

- `tmux://sessions` — All sessions
- `tmux://sessions/{session_name}` — Session detail with windows
- `tmux://sessions/{session_name}/windows` — Session's windows
- `tmux://sessions/{session_name}/windows/{window_index}` — Window detail with panes
- `tmux://panes/{pane_id}` — Pane details
- `tmux://panes/{pane_id}/content` — Pane captured content
