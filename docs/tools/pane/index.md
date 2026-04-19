# Pane tools

Pane-scoped tools — read and drive individual terminals, wait for output, copy mode, and channel sync.

::::{grid} 1 2 2 3
:gutter: 2 2 3 3

:::{grid-item-card} capture_pane
:link: capture-pane
:link-type: ref
Read visible or scrollback text from a pane.
:::

:::{grid-item-card} search_panes
:link: search-panes
:link-type: ref
Search text across many panes in one call.
:::

:::{grid-item-card} snapshot_pane
:link: snapshot-pane
:link-type: ref
Capture content plus cursor, mode, and scroll state in one call.
:::

:::{grid-item-card} get_pane_info
:link: get-pane-info
:link-type: ref
Read pane metadata without content.
:::

:::{grid-item-card} display_message
:link: display-message
:link-type: ref
Evaluate a tmux format string against a target.
:::

:::{grid-item-card} send_keys
:link: send-keys
:link-type: ref
Send keystrokes or commands to a pane.
:::

:::{grid-item-card} paste_text
:link: paste-text
:link-type: ref
Paste multi-line text via tmux buffer.
:::

:::{grid-item-card} pipe_pane
:link: pipe-pane
:link-type: ref
Fork pane output to a file or program.
:::

:::{grid-item-card} select_pane
:link: select-pane
:link-type: ref
Switch focus to a pane.
:::

:::{grid-item-card} swap_pane
:link: swap-pane
:link-type: ref
Swap two panes' positions.
:::

:::{grid-item-card} set_pane_title
:link: set-pane-title
:link-type: ref
Set a pane's human-readable title.
:::

:::{grid-item-card} clear_pane
:link: clear-pane
:link-type: ref
Clear a pane's scrollback.
:::

:::{grid-item-card} resize_pane
:link: resize-pane
:link-type: ref
Resize a pane.
:::

:::{grid-item-card} enter_copy_mode
:link: enter-copy-mode
:link-type: ref
Enter tmux copy mode for scrollback navigation.
:::

:::{grid-item-card} exit_copy_mode
:link: exit-copy-mode
:link-type: ref
Exit copy mode.
:::

:::{grid-item-card} wait_for_text
:link: wait-for-text
:link-type: ref
Block until a pattern appears in a pane.
:::

:::{grid-item-card} wait_for_content_change
:link: wait-for-content-change
:link-type: ref
Block until pane content changes.
:::

:::{grid-item-card} wait_for_channel
:link: wait-for-channel
:link-type: ref
Block until a tmux wait-for channel is signalled.
:::

:::{grid-item-card} signal_channel
:link: signal-channel
:link-type: ref
Signal a waiting channel.
:::

:::{grid-item-card} kill_pane
:link: kill-pane
:link-type: ref
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
kill-pane
```
