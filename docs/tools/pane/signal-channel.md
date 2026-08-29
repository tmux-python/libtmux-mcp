# Signal channel

```{fastmcp-tool} wait_for_tools.signal_channel
```

**Use when** you need to wake a blocked {tooliconl}`wait-for-channel`
caller from a different MCP context (e.g. when a long-running task in
one pane completes and another pane should proceed).

**Signal exactly once per channel.** tmux *latches* a signal nobody is
waiting for, which is what makes this better than polling — signal
first, wait later, and "did it finish?" becomes a question about the
past. A **second** signal on a latched channel with no waiter destroys
the channel, latch and all, and the next wait blocks to its ceiling. It
toggles rather than saturating:

| signals, then wait | result |
|---|---|
| 1 | returns in 0.04s |
| 2 | **blocks** — latch cleared |
| 3 | returns in 0.03s |

The habit that breaks it is the careful one: a `wait-for -S done` at the
end of the command *and* another in a cleanup or trap. Put it in exactly
one place.

This is tmux's own behaviour and the server does not paper over it —
reading the latch first is a race, and there is no non-destructive way
to ask.

**Side effects:** Wakes any clients blocked on the named channel.
Doesn't allocate or persist state.

```{fastmcp-tool-input} wait_for_tools.signal_channel
```
