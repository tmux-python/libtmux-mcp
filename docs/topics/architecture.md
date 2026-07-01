(architecture)=

# Architecture

For contributors who need to understand the codebase internals.

## Source layout

```
src/libtmux_mcp/
    __init__.py           # Entry point: main()
    __main__.py           # python -m libtmux_mcp support
    server.py             # FastMCP instance and configuration
    _utils.py             # Server caching, resolvers, serializers, error handling
    models.py             # Pydantic output models
    middleware.py         # Safety, audit, retry, and error-result middleware
    tools/
        batch_tools.py    # call_readonly_tools_batch, call_mutating_tools_batch, call_destructive_tools_batch
        server_tools.py   # list_servers, list_sessions, create_session, kill_server, get_server_info
        session_tools.py  # list_windows, create_window, rename_session, kill_session
        window_tools.py   # list_panes, split_window, rename_window, kill_window, select_layout, resize_window
        pane_tools.py     # run_command, send_keys, send_keys_batch, capture_pane, capture_since, snapshot_pane, search_panes, wait_for_text
        buffer_tools.py   # load_buffer, paste_buffer, show_buffer, delete_buffer
        hook_tools.py     # show_hooks, show_hook
        option_tools.py   # show_option, set_option
        env_tools.py      # show_environment, set_environment
    resources/
        hierarchy.py      # tmux:// URI resources
```

## Request flow

Middleware wraps tool calls outermost-first (full ordering rationale in
the `server.py` stack comment):

```
MCP Client (Claude, Cursor, etc.)
  → stdio transport
    → FastMCP server (server.py)
      → TimingMiddleware (wall-time observer)
        → TailPreservingResponseLimitingMiddleware (response size backstop)
          → ToolErrorResultMiddleware (exceptions → is_error results)
            → AuditMiddleware (one log record per call)
              → ReadonlyRetryMiddleware (retries readonly tools only)
                → SafetyMiddleware (tier gate, fail-closed)
                  → Tool function (tools/*.py)
                    → libtmux Python objects
                      → tmux binary (via subprocess)
```

The libtmux layer is the tmux object hierarchy:
{class}`~libtmux.Server`, {class}`~libtmux.Session`,
{class}`~libtmux.Window`, and {class}`~libtmux.Pane`.

## Key design decisions

### Tool registration

Each tool module defines a `register(mcp)` function that registers tools with metadata:
- `title` — human-readable name
- `annotations` — MCP tool annotations (readOnlyHint, destructiveHint, idempotentHint)
- `tags` — safety tier tags for middleware filtering

### Server caching

{mod}`libtmux_mcp._utils` maintains a thread-safe cache keyed by
`(socket_name, socket_path, tmux_bin)`. Dead servers are evicted on
access via {meth}`libtmux.Server.is_alive` checks.

### Object resolution

Tools use resolver functions ({func}`~libtmux_mcp._utils._resolve_session`,
{func}`~libtmux_mcp._utils._resolve_window`, and
{func}`~libtmux_mcp._utils._resolve_pane`) that accept multiple
targeting parameters and resolve to the correct
{external+libtmux:doc}`libtmux <index>` object. Resolution follows a
priority chain: direct ID → name lookup → error.

### Safety middleware

{class}`~libtmux_mcp.middleware.SafetyMiddleware` implements
[FastMCP](https://gofastmcp.com)'s middleware interface. It operates
as a secondary gate behind FastMCP's native tag visibility system,
providing clear error messages when a tool above the configured tier
is invoked.

### Error handling

Three boundaries split the work:

1. **Tool classification** — the {func}`~libtmux_mcp._utils.handle_tool_errors` decorator wraps tool functions, mapping {external+libtmux:doc}`libtmux <index>` exceptions to {exc}`~libtmux_mcp._utils.ExpectedToolError` (agent-correctable: unknown ids, invalid arguments, transient tmux errors; logged at WARNING) or FastMCP tool errors (operator faults and unexpected bugs; logged at ERROR). The raise chains the original exception via `from e`, which is what lets {class}`~libtmux_mcp.middleware.ReadonlyRetryMiddleware` match transient {exc}`~libtmux.exc.LibTmuxException` causes.
2. **Schema classification** — FastMCP validates tool arguments before tool code runs, so [Pydantic](https://docs.pydantic.dev/) validation failures never reach the decorator. {class}`~libtmux_mcp.middleware.ToolErrorResultMiddleware` classifies those schema-validation errors as expected, agent-correctable WARNINGs before converting them.
3. **Conversion** — {class}`~libtmux_mcp.middleware.ToolErrorResultMiddleware` catches the exception once it has cleared the audit/retry/safety trio and returns an error `ToolResult` carrying the message exactly as raised, plus a `_meta` payload (`error_type`, `expected`, and an optional agent-facing `suggestion` for recovery hints such as discovery tools or rejected-argument fixes).

Errors must stay exceptions through the audit/retry/safety trio — audit detects failures by catching, retry matches via `__cause__` — so conversion happens only in the outermost error layer. The response limiter sits outside conversion and may truncate large success or error results on the return path; its truncation path preserves `is_error` and `_meta` so oversized expected failures stay tool errors. Level policy lives in {doc}`/topics/logging`.

## References

- {external+libtmux:doc}`libtmux <index>` — Core tmux Python library
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [MCP Specification](https://modelcontextprotocol.io/) — Model Context Protocol
- [tmux man page](http://man.openbsd.org/OpenBSD-current/man1/tmux.1)
