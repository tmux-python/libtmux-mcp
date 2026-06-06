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
        server_tools.py   # list_sessions, create_session, kill_server, get_server_info
        session_tools.py  # list_windows, create_window, rename_session, kill_session
        window_tools.py   # list_panes, split_window, rename_window, kill_window, select_layout, resize_window
        pane_tools.py     # send_keys, capture_pane, capture_since, resize_pane, kill_pane, set_pane_title, get_pane_info, clear_pane, search_panes, wait_for_text
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
                    → libtmux Server/Session/Window/Pane
                      → tmux binary (via subprocess)
```

## Key design decisions

### Tool registration

Each tool module defines a `register(mcp)` function that registers tools with metadata:
- `title` — human-readable name
- `annotations` — MCP tool annotations (readOnlyHint, destructiveHint, idempotentHint)
- `tags` — safety tier tags for middleware filtering

### Server caching

`_utils.py` maintains a thread-safe cache keyed by `(socket_name, socket_path, tmux_bin)`. Dead servers are evicted on access via `is_alive()` checks.

### Object resolution

Tools use resolver functions (`_resolve_session`, `_resolve_window`, `_resolve_pane`) that accept multiple targeting parameters and resolve to the correct libtmux object. Resolution follows a priority chain: direct ID → name lookup → error.

### Safety middleware

`SafetyMiddleware` implements FastMCP's middleware interface. It operates as a secondary gate behind FastMCP's native tag visibility system, providing clear error messages when a tool above the configured tier is invoked.

### Error handling

Two layers split the work:

1. **Classification** — the `@handle_tool_errors` decorator wraps all tool functions, mapping libtmux exceptions to `ExpectedToolError` (agent-correctable: unknown ids, invalid arguments, transient tmux errors; logged at WARNING) or stock `ToolError` (operator faults and unexpected bugs; logged at ERROR). The raise chains the original exception via `from e`, which is what lets `ReadonlyRetryMiddleware` match transient `LibTmuxException` causes.
2. **Conversion** — `ToolErrorResultMiddleware` catches the exception once it has cleared the audit/retry/safety trio and returns `ToolResult(is_error=True)` carrying the message exactly as raised, plus a `_meta` payload (`error_type`, `expected`, optional `suggestion` pointing at discovery tools).

Errors must stay exceptions through the audit/retry/safety trio — audit detects failures by catching, retry matches via `__cause__` — so conversion happens only in the outermost error layer. Level policy lives in {doc}`/topics/logging`.

## References

- [libtmux](https://libtmux.git-pull.com/) — Core tmux Python library
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server framework
- [MCP Specification](https://modelcontextprotocol.io/) — Model Context Protocol
- [tmux man page](http://man.openbsd.org/OpenBSD-current/man1/tmux.1)
