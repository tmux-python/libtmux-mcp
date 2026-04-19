# Signal channel

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
