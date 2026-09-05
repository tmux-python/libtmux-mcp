(completion-overview)=

# Completion

The
[MCP completion](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion)
protocol lets clients ask a server for argument suggestions. libtmux-mcp
answers it for the ``tmux://`` resource templates, offering the session
names, window indexes and pane ids the live server currently has.

## What the spec does

A client calls ``completion/complete`` with a partial argument for a
prompt or resource URI template; the server replies with up to 100
suggestions. Agents use this to offer auto-complete UX — e.g. a
session picker popup when filling ``session_name=`` on
``get_session``.

## What libtmux-mcp currently exposes

- **Resource template parameters** — {doc}`/resources` URIs carry
  ``{session_name}``, ``{window_index}``, ``{pane_id}`` and
  ``{?socket_name}``. The first three complete from the live server,
  filtered by what has been typed. Supplying ``socket_name`` first
  scopes the suggestions to that server; a server that is gone yields
  no suggestions rather than an error.
- **Prompt arguments** — the four recipes ({doc}`/prompts`) advertise
  their argument names and types through their schemas. They take free
  text rather than tmux identifiers, so they have no domain to
  enumerate.

MCP publishes a template's URI but never its parameter domain, so
without this a reader has no way to discover a valid session name or
pane id from the template listing alone.

```{warning}
Whether you see suggestions depends on the client, not the server.
Completion serves human-facing hosts — the MCP Inspector's browser UI
and VS Code send ``completion/complete``. Measured against five agent
CLIs, none send it, so an agent sees no difference.
```

## Enumerating from a client that does not complete

Agents that need to pick a real session / window / pane can call
{tool}`list-sessions`, {tool}`list-windows`, or {tool}`list-panes`
directly before rendering a prompt, then feed the chosen ID back
into the prompt's arguments.

## Further reading

- [MCP completion spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion)
- {doc}`/prompts` — the prompt argument surface
- {doc}`/resources` — the resource-template parameter surface
