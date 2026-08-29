(architecture)=

# Architecture

For contributors who need to understand the codebase internals.

## Source layout

```
src/libtmux_mcp/
    __init__.py           # Entry point: main()
    __main__.py           # python -m libtmux_mcp support
    server.py             # FastMCP instance, safety tier, instructions budget
    models.py             # Pydantic output models
    middleware.py         # Safety, audit, retry, and error-result middleware
    _utils.py             # Server cache, resolvers, serializers, error handling
    _tmux_proc.py         # Cancellable, bounded tmux subprocess (see below)
    _bounded_io.py        # Bounded tmux reads shared by the async tools
    _patterns.py          # Caller-regex screening
    _progress.py          # Progress ticker for waits with no poll loop
    _history.py           # Shell-history suppression
    _wait_policy.py       # Wait ceiling resolution
    tools/
        batch_tools.py    # call_{readonly,mutating,destructive}_tools_batch
        server_tools.py   # list_servers, list_sessions, create_session, ...
        session_tools.py  # list_windows, create_window, rename_session, ...
        window_tools.py   # list_panes, split_window, break_pane, join_pane, ...
        buffer_tools.py   # load_buffer, paste_buffer, show_buffer, delete_buffer
        hook_tools.py     # show_hooks, show_hook
        option_tools.py   # show_option, set_option
        env_tools.py      # show_environment, set_environment, unset_environment
        wait_for_tools.py # wait_for_channel, signal_channel
        pane_tools/       # split by operation kind, not one file per tool
            io.py         # send_keys, paste_text, run_command, capture_pane
            wait.py       # wait_for_text
            capture_since.py  # incremental capture and its cursor
            state.py      # the one pane-state read every tool shares
            meta.py       # get_pane_info, snapshot_pane, display_message
            layout.py     # select_pane, resize_pane, swap_pane
            lifecycle.py  # respawn_pane, kill_pane
            copy_mode.py  # enter_copy_mode, exit_copy_mode
            pipe.py       # pipe_pane
            search.py     # search_panes, find_pane_by_position
    prompts/recipes.py    # The four workflow prompts
    resources/hierarchy.py  # tmux:// URI resources
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

### Reaching tmux from an async tool

Every async tool goes through `_tmux_proc`, which owns a tmux
subprocess it can kill. Neither of the obvious alternatives works:

- **Calling libtmux inline** blocks the event loop. It reaches tmux
  through `Popen.communicate()` with no timeout, so every other
  in-flight call waits, and against a server that has stopped
  answering, waits indefinitely.
- **Wrapping it in `asyncio.to_thread`** frees the loop and creates a
  worse failure. The coroutine takes the cancellation immediately while
  the worker stays blocked, and
  `concurrent.futures.thread._python_exit` joins pool workers untimed
  at shutdown — so one wedged tmux takes process exit and Ctrl-C with
  it. The loop keeps ticking throughout, which is why no
  loop-blocking test can see this.

`asyncio.to_thread` stays correct for **bounded** work. `_run_send_keys`
runs each argv under a timeout, so its worker always returns. The
hazard is the untimed call, not the thread.

Seeing the failure at all needs a socket that answers its FIRST
connection and stalls the rest: one that never answers is caught by the
bounded liveness probe before the unbounded call is reached, so a
fixture built the obvious way comes back confidently clean.

`tests/test_pane_tools.py` enforces the rule structurally rather than
by measurement — it reads the tree for a blocking call made inline from
an async body.

### The bound under the synchronous tools

Most tools are plain `def`, which FastMCP runs on a worker thread. That
keeps the event loop alive but does nothing about the wait: libtmux
still reaches tmux through an untimed `Popen.communicate()`, so a tool
that touched a stalled server never returned at all. `break_pane` makes
eleven round trips and was measured still running at 150 seconds.

The liveness probe does not help here. It bounds the FIRST round trip
and nothing after it, so a socket that answers the probe and stalls
afterwards walks straight past it.

The bound is installed at `tmux_cmd` itself, in `_utils`, and rebound
into every libtmux module that constructs one. `Server.cmd` is not the
only funnel — `neo.fetch_objs` builds a `tmux_cmd` directly and is the
engine behind `Window.panes` and `Session.windows`, so bounding
`Server.cmd` alone leaves the busiest path unbounded. A test AST-walks
the installed libtmux and fails if a call site appears outside the
bound set, because a rebind that stops applying does so silently.

Why it matters more than one slow call: a hung call never returned its
worker, and cancelling the coroutine did not interrupt it. Forty
accumulated hung calls — concurrently, or one at a time with a cancel
between each — exhaust anyio's default thread limiter, and the server
then stops answering everything, including healthy sockets. Forty is
reached by an agent behaving correctly: call, give up, retry. The bound
returns the worker, and `subprocess.run` kills and reaps the tmux
client, so neither threads nor processes accumulate. Measured at 64
concurrent stalled calls — 60% past the pool limit that used to be
fatal — every one returns a bounded error and no client is left behind.

Two consequences of a bounded pool, both real:

- **Unrelated calls can wait for one bound.** While stalled calls hold
  every worker, a call to a perfectly healthy socket waits for a worker
  to come free, and the first one frees when the first stalled call
  times out. Measured at 4.2s for the first such call, then ~25ms for
  every one after. So a stalled tmux server costs other sockets up to
  one timeout of latency, not zero.
- **Cancellation does not reap; the bound does.** Cancelling the request
  cannot interrupt a thread already inside `communicate()`, so the tmux
  client dies when the bound fires, not when the caller gives up.
  Measured: 16 cancelled calls still had 16 clients at +4s and zero at
  +7s. An agent that cancels and retries immediately still stacks — but
  the window is one bound wide instead of unbounded, which is the whole
  difference between annoying and fatal.

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
