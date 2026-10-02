# Wait for pane exit

Blocks until the process tmux started in a pane ends, then reports its exit
status or terminating signal. It waits for the pane's own process, the one
given to {tooliconl}`split-window` or {tooliconl}`respawn-pane` as `shell`, not
for a command typed into an interactive shell. For a typed command use
{tooliconl}`run-command`.

```python
pane = split_window(shell="make test")
wait_for_pane_exit(pane_id=pane.pane_id, timeout=120)
# PaneExitResult(exited=True, exit_status=2, ...)
```

The pane stays on screen as a dead pane after the wait, so
{tooliconl}`capture-pane` can read its output; remove it with
{tooliconl}`kill-pane`. A pane whose process ended before the call has already
closed and cannot be waited on, so a very short job needs `remain-on-exit`
set before it starts.

```{fastmcp-tool} pane_tools.wait_for_pane_exit
```

**Use when** you started a one-shot job in its own pane and need to know
whether it succeeded, without a prompt or a marker to match.

**Avoid when** the command runs in an interactive shell. Use
{tooliconl}`run-command`, or {tooliconl}`wait-for-channel` for custom
completion.

**Side effects:** Sets the pane's `remain-on-exit` option for the duration of
the wait and restores it. Blocks up to `timeout` seconds; a pane still running
at expiry returns `timed_out=true`.

```{fastmcp-tool-input} pane_tools.wait_for_pane_exit
```
