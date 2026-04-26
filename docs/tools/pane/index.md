# Pane tools

Pane-scoped tools — read and drive individual terminals, wait for output, copy mode, and channel sync.

::::{grid} 1 2 2 3
:gutter: 2 2 3 3

:::{grid-item-card} {tooliconl}`capture-pane`
Read visible or scrollback text from a pane.
:::

:::{grid-item-card} {tooliconl}`search-panes`
Search text across many panes in one call.
:::

:::{grid-item-card} {tooliconl}`snapshot-pane`
Capture content plus cursor, mode, and scroll state in one call.
:::

:::{grid-item-card} {tooliconl}`get-pane-info`
Read pane metadata without content.
:::

:::{grid-item-card} {tooliconl}`display-message`
Evaluate a tmux format string against a target.
:::

:::{grid-item-card} {tooliconl}`send-keys`
Send keystrokes or commands to a pane.
:::

:::{grid-item-card} {tooliconl}`paste-text`
Paste multi-line text via tmux buffer.
:::

:::{grid-item-card} {tooliconl}`pipe-pane`
Fork pane output to a file or program.
:::

:::{grid-item-card} {tooliconl}`select-pane`
Switch focus to a pane.
:::

:::{grid-item-card} {tooliconl}`swap-pane`
Swap two panes' positions.
:::

:::{grid-item-card} {tooliconl}`set-pane-title`
Set a pane's human-readable title.
:::

:::{grid-item-card} {tooliconl}`clear-pane`
Clear a pane's scrollback.
:::

:::{grid-item-card} {tooliconl}`resize-pane`
Resize a pane.
:::

:::{grid-item-card} {tooliconl}`enter-copy-mode`
Enter tmux copy mode for scrollback navigation.
:::

:::{grid-item-card} {tooliconl}`exit-copy-mode`
Exit copy mode.
:::

:::{grid-item-card} {tooliconl}`wait-for-text`
Block until a pattern appears in a pane.
:::

:::{grid-item-card} {tooliconl}`wait-for-content-change`
Block until pane content changes.
:::

:::{grid-item-card} {tooliconl}`wait-for-channel`
Block until a tmux wait-for channel is signalled.
:::

:::{grid-item-card} {tooliconl}`signal-channel`
Signal a waiting channel.
:::

:::{grid-item-card} {tooliconl}`respawn-pane`
Restart a pane's process in place, preserving pane_id.
:::

:::{grid-item-card} {tooliconl}`kill-pane`
Terminate a pane. Destructive.
:::

::::

```{toctree}
:hidden:
:maxdepth: 1

capture-pane
search-panes
snapshot-pane
get-pane-info
display-message
send-keys
paste-text
pipe-pane
select-pane
swap-pane
set-pane-title
clear-pane
resize-pane
enter-copy-mode
exit-copy-mode
wait-for-text
wait-for-content-change
wait-for-channel
signal-channel
respawn-pane
kill-pane
```
