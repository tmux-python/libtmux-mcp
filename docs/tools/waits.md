# Wait-for channels

tmux's `wait-for` command exposes named, server-global channels that
clients can signal and block on. These give agents an explicit
synchronization primitive — strictly cheaper in agent turns than
polling pane content via {tooliconl}`capture-pane` or
{tooliconl}`wait-for-text`.

The composition pattern: `send_keys` a command that emits the signal
on its exit, then `wait_for_channel`. The signal MUST fire on both
success and failure paths or the wait will block until the timeout.

```python
send_keys(
    pane_id="%1",
    keys="pytest; status=$?; tmux wait-for -S tests_done; exit $status",
)
wait_for_channel("tests_done", timeout=60)
```

The `; status=$?; tmux wait-for -S NAME; exit $status` idiom is the
load-bearing safety contract — `wait-for` is edge-triggered, so a
crash before the signal would deadlock until the wait's `timeout`.

## Block

```{fastmcp-tool} wait_for_tools.wait_for_channel
```

**Use when** the shell command can reliably emit the signal (single
test runs, build scripts, dev-server boot, anything composable with
`; status=$?; tmux wait-for -S name; exit $status`).

**Avoid when** the signal cannot be guaranteed — for example, when
the command might be killed externally. Use {tooliconl}`wait-for-text`
to poll for an output marker instead; state-polling is structurally
safer than edge-triggered signalling for fragile commands.

**Side effects:** Blocks the call up to `timeout` seconds (default 30).
Mandatory subprocess timeout — a crashed signaller raises `ToolError`
rather than blocking indefinitely.

```{fastmcp-tool-input} wait_for_tools.wait_for_channel
```

---

## Signal

```{fastmcp-tool} wait_for_tools.signal_channel
```

**Use when** you need to wake a blocked {tooliconl}`wait-for-channel`
caller from a different MCP context (e.g. when a long-running task in
one pane completes and another pane should proceed). Signalling an
unwaited channel is a successful no-op — safe to call defensively.

**Side effects:** Wakes any clients blocked on the named channel.
Doesn't allocate or persist state.

```{fastmcp-tool-input} wait_for_tools.signal_channel
```
