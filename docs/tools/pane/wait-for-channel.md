# Wait for channel

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
