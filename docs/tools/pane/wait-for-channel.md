# Wait for channel

tmux's `wait-for` command exposes named, server-global channels that
clients can signal and block on. These give agents an explicit
synchronization primitive for custom shell composition. For the common
"run this shell command and report the result" workflow, prefer
{tooliconl}`run-command`, which wraps this pattern and returns exit
status plus output.

The composition pattern: {tooliconl}`send-keys` a command followed by `; tmux wait-for -S NAME`, then call {tooliconl}`wait-for-channel`. Shell `;` semantics fire the second statement whether the first succeeds or fails, so the edge-triggered signal never deadlocks the agent on a crashed command.

```python
send_keys(
    pane_id="%1",
    keys="pytest; tmux wait-for -S tests_done",
)
wait_for_channel("tests_done", timeout=60)
```

The `; tmux wait-for -S NAME` suffix is the load-bearing safety contract — `wait-for` is edge-triggered, so a crash before the signal would deadlock until the wait's `timeout`. The shell separator `;` runs the next statement unconditionally, so the signal fires on both success and failure paths.

The payload deliberately does not append `exit $?` — in an interactive
shell that exits the shell itself, taking single-pane sessions down
with it. If exit-status preservation matters and the command fits the
standard one-shot shape, use {tooliconl}`run-command`.

```{fastmcp-tool} wait_for_tools.wait_for_channel
```

**Use when** the shell command can reliably emit the signal and
{tooliconl}`run-command` does not fit the desired shell composition
(single test runs, build scripts, dev-server boot, anything composable
with `; tmux wait-for -S name`).

**Avoid when** the signal cannot be guaranteed — for example, when
the command might be killed externally. Use {tooliconl}`wait-for-text`
to poll for an output marker instead; state-polling is structurally
safer than edge-triggered signalling for fragile commands.

**Side effects:** Blocks the call up to `timeout` seconds (default 30).
Mandatory subprocess timeout — a crashed signaller raises an
{exc}`~libtmux_mcp._utils.ExpectedToolError` rather than blocking
indefinitely.

```{fastmcp-tool-input} wait_for_tools.wait_for_channel
```
