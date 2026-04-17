(logging-overview)=

# Logging

libtmux-mcp uses Python's standard ``logging`` module under the
``libtmux_mcp.*`` namespace. FastMCP forwards server-side log
records to connected MCP clients via the
[MCP logging capability](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/logging).
No manual wiring needed.

## Logger hierarchy

All loggers are children of ``libtmux_mcp``. The primary streams
are:

- ``libtmux_mcp.audit`` — one structured line per tool call, emitted
  by {class}`~libtmux_mcp.middleware.AuditMiddleware`. Includes
  tool name, digest-redacted arguments, latency, outcome. See
  {doc}`/topics/safety` for the argument-redaction rules.
- ``libtmux_mcp.retry`` — warnings from
  {class}`~libtmux_mcp.middleware.ReadonlyRetryMiddleware` when a
  readonly tool retried after a transient
  {exc}`libtmux.exc.LibTmuxException`.
- ``libtmux_mcp.server`` / ``libtmux_mcp.tools.*`` / etc. — ad-hoc
  warnings and debug messages from the codebase.

## Level control

Set the logger level via standard Python mechanisms — for local
development, the simplest is an environment variable:

```console
$ FASTMCP_LOG_LEVEL=DEBUG libtmux-mcp
```

FastMCP reads ``FASTMCP_LOG_LEVEL`` at startup and applies it to
every ``fastmcp.*`` and ``libtmux_mcp.*`` logger.

## What clients see

MCP clients render incoming ``notifications/message`` records in
their log pane (e.g. Claude Desktop's "MCP server logs" panel, or
``claude-cli``'s ``--verbose`` output). The records include the
server name (``libtmux-mcp``), level, and the log message — but not
the Python logger name, which the protocol doesn't model.

```{tip}
If a tool call fails silently (no user-visible error, no side
effect), the ``libtmux_mcp.audit`` log will show the invocation and
its return value. That's usually the fastest way to tell whether a
tool ran at all.
```

## Further reading

- [MCP logging spec](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/logging)
- {doc}`/topics/safety` — audit log redaction rules
- {class}`~libtmux_mcp.middleware.AuditMiddleware` — the primary
  audit emitter
