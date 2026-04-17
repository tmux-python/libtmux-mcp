(completion-overview)=

# Completion

libtmux-mcp inherits FastMCP's built-in
[MCP completion](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion)
behaviour. We don't hand-author completion providers — the argument
shapes on our prompts and resource templates are what the client
sees.

## What the spec does

A client calls ``completion/complete`` with a partial argument for a
prompt or resource URI template; the server replies with up to 100
suggestions. Agents use this to offer auto-complete UX — e.g. a
session picker popup when filling ``session_name=`` on
``get_session``.

## What libtmux-mcp currently exposes

- **Prompt arguments** — the four recipes ({doc}`/tools/prompts`)
  advertise their argument names and types. FastMCP derives a default
  completion shape from the Python signatures:
  ``str`` arguments accept free text, ``float`` arguments accept
  numeric strings, no enum / list suggestions.
- **Resource template parameters** —
  {doc}`/reference/api/resources` URIs carry ``{session_name}``,
  ``{window_index}``, ``{pane_id}``, and ``{?socket_name}``
  placeholders. Completion suggestions are again derived from the
  function signatures' types, not from live tmux state.

```{warning}
libtmux-mcp does **not** currently wire completion back to live
tmux enumeration — i.e. the completion for ``session_name`` will not
return the names of sessions that exist on the server right now.
Adding that requires a dedicated FastMCP completion handler;
tracked as a potential enhancement.
```

## Workarounds for clients that need live enumeration

Agents that need to pick a real session / window / pane can call
{tool}`list-sessions`, {tool}`list-windows`, or {tool}`list-panes`
directly before rendering a prompt, then feed the chosen ID back
into the prompt's arguments.

## Further reading

- [MCP completion spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion)
- {doc}`/tools/prompts` — the prompt argument surface
- {doc}`/reference/api/resources` — the resource-template parameter surface
