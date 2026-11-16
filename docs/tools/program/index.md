# Owned programs

Run an executable with literal arguments in a fresh PTY, read its exit and
rendered output, then remove its pane. The borrowed session and its existing
panes survive. Use {tool}`run-program` for a bounded command on tmux 3.8+.
Use {tool}`run-command` when the command needs an existing interactive shell.

This development branch depends on the source revision of
[libtmux core PR #833](https://github.com/tmux-python/libtmux/pull/833) pinned in
`pyproject.toml` and `uv.lock`. It uses no experimental engine package. Release
is held until that core API is published and the dependency uses its release.

Discover a stable session ID with {tool}`list-sessions`, then check
{tool}`get-server-capabilities`. Direct Python callers use the same async tool:

```python
>>> import asyncio
>>> import sys
>>> from libtmux_mcp.tools.program_tools import run_program
>>> from libtmux_mcp.tools.server_tools import get_server_capabilities
>>> report = asyncio.run(get_server_capabilities(socket_name=server.socket_name))
>>> if any(item.name == "program-owned" and item.status == "supported" for item in report.features):
...     result = asyncio.run(run_program(
...         session_id=session.session_id,
...         socket_name=server.socket_name,
...         argv=[sys.executable, "-c", "print('finished'); raise SystemExit(7)"],
...     ))
...     assert result.exit_status == 7
...     assert result.screen_text == "finished"
...     assert result.pane_removed
```

## Cancellation and recovery

Request cancellation signals the native worker, which attempts cleanup before
the server handler settles. An MCP client can acknowledge cancellation before
remote cleanup finishes. {tool}`list-program-runs` reports running workers and
retained cleanup summaries in pages of four (at most sixteen).
Use {tool}`get-program-run` to inspect one owner's bounded error and prior-output
details. Records survive request cancellation, including
repeated cancellation during cleanup.

Failed calls include `program_cleanup_errors` and any `program_observation` in
MCP error metadata. If cleanup remains pending, `program_recovery_id` identifies
the original owner. Call {tool}`cleanup-program` with that ID after correcting
the cause. Retry is serialized and rechecks the original ownership. Missing
receipts or replaced lifetimes refuse cleanup; a pane ID alone cannot establish
ownership. Do not infer that a missing receipt means creation failed.

At most 32 running, queued or unresolved owners share capacity. New work is
refused before effects when capacity is full. Records are never evicted to make
room. They exist in this MCP process and do not survive a process restart.
An unverified receipt can require operator intervention; this API cannot adopt
or force-delete an arbitrary pane to settle the record.

## Output and lifetime limits

The result is a tail of rendered terminal history and screen. Stdout and stderr
share the PTY. `screen_truncated` reports clipping by the MCP byte bound;
`history_complete` remains `null` because tmux may have discarded earlier
history. A core capture exceeding its own bound fails rather than returning
unmarked loss. This initial tool does not expose raw streaming or a persistent
interactive handle.

`timeout` is clamped to `LIBTMUX_MCP_WAIT_MAX_SECONDS`; successful results report
`effective_timeout`. Cleanup has separate finite budgets and can continue after
the operation deadline. Pane removal closes the PTY but does not prove that
every descendant exited. Terminal ownership does not restrict filesystem,
network or privilege access, and it provides no controller-crash lease.

```{toctree}
:maxdepth: 1

run-program
list-program-runs
get-program-run
cleanup-program
```
