(completion-overview)=

# Completion

The
[MCP completion](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion)
protocol lets clients ask a server for argument suggestions. libtmux-mcp
does not currently register custom completion handlers.

## What the spec does

A client calls ``completion/complete`` with a partial argument for a
prompt or resource URI template; the server replies with up to 100
suggestions. Agents use this to offer auto-complete UX — e.g. a
session picker popup when filling ``session_name=`` on
``get_session``.

## What libtmux-mcp currently exposes

- **Prompt arguments** — the four recipes ({doc}`/prompts`)
  advertise their argument names and types through their schemas.
- **Resource template parameters** —
  {doc}`/resources` URIs carry ``{session_name}``,
  ``{window_index}``, ``{pane_id}``, and ``{?socket_name}``
  placeholders.

```{warning}
Clients should not rely on ``completion/complete`` returning live tmux
suggestions, schema-derived examples, or enum-like values today.
Adding live suggestions requires dedicated completion handlers.
```

## Workarounds for clients that need live enumeration

Agents that need to pick a real session / window / pane can call
{tool}`list-sessions`, {tool}`list-windows`, or {tool}`list-panes`
directly before rendering a prompt, then feed the chosen ID back
into the prompt's arguments.

## Further reading

- [MCP completion spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion)
- {doc}`/prompts` — the prompt argument surface
- {doc}`/resources` — the resource-template parameter surface
