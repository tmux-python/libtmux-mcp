# Run tmux plan

```{fastmcp-tool} chain_tools.run_tmux_plan
```

**Use when** you need several typed tmux operations to run in order over
one persistent tmux control connection, with a typed result per step.

**Avoid when** you need to call arbitrary MCP tools; use
{tooliconl}`call-mutating-tools-batch` for that. Use individual tools
when a workflow has only one step.

**Execution:** Each operation is dispatched on its own over a persistent
`tmux -C` control connection, so every operation keeps its own result. A
`split_pane` with a `ref` returns the new pane ID in `created_panes`, and
later operations can target it with a `ref` target.

**Targets:** Each pane operation takes one typed `target`, discriminated by
`kind`: `pane_id` (a concrete `%id`) or `ref` (a name minted by an earlier
`split_pane`).

**Layouts:** `split_evenly` splits a pane into an even row or column of
`count` panes, and `make_grid` tiles a pane's window into a `rows` by `cols`
grid. Both compile to native splits plus a `select-layout`; use the raw
`select_layout` operation for any other tmux layout.

**Results:** `steps` carries one typed result per operation, discriminated
by `kind`: `capture_pane` returns its `lines`, `split_pane` returns the
new `pane_id`, and the rest return status only. Each step also carries an
`error` message when it fails. Pass `explain` to attach per-dispatch
diagnostics (rendered argv and raw stdout/stderr) under `diagnostics`.

**Side effects:** Mutates tmux state according to the submitted
operation list. With `on_error="stop"` (the default), the tool stops
before the next operation once one fails or its target cannot be
resolved, and marks the rest `skipped`. With `on_error="continue"`,
every failure is recorded and the rest still run.

Set `dry_run` to `true` to compile the operation list and return the
rendered dispatches without touching tmux. Referenced split panes use
deterministic placeholders in `created_panes` until the plan is run for
real.

`dispatch_timeout` defaults to 10 seconds and bounds how long the tool
waits for each native tmux dispatch. A timed-out dispatch marks the
operation failed with `returncode: null`; because dispatches run in a
worker thread, the underlying tmux work may still finish after the tool
returns.

Set `rollback_on_error` to `true` to kill panes created by
ref-producing `split_pane` operations when the overall operation list
fails. The result still reports `created_panes`, and adds
`rolled_back_panes` plus `rollback_errors` for cleanup visibility.

**Example:**

```json
{
  "tool": "run_tmux_plan",
  "arguments": {
    "operations": [
      {"kind": "split_pane", "target": {"kind": "pane_id", "pane_id": "%1"},
       "ref": "work"},
      {"kind": "send_keys", "target": {"kind": "ref", "ref": "work"},
       "keys": "uv run pytest"}
    ],
    "on_error": "stop"
  }
}
```

```{fastmcp-tool-input} chain_tools.run_tmux_plan
```
