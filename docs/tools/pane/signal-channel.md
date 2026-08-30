# Signal channel

```{fastmcp-tool} wait_for_tools.signal_channel
```

**Use when** you need to wake a blocked {tooliconl}`wait-for-channel`
caller from a different MCP context (e.g. when a long-running task in
one pane completes and another pane should proceed). With no current
waiter, tmux records one pending signal for the next waiter. A second
signal before that wait clears the channel.

**Side effects:** Wakes clients blocked on the named channel or records one
pending signal for the next waiter to consume. A waiter resumes whatever work
follows its wait.

```{fastmcp-tool-input} wait_for_tools.signal_channel
```
