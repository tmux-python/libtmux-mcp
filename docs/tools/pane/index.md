# Pane

Tools for pane-level operations: reading content, sending input, navigation, scrollback, and lifecycle.

## Inspect

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} capture_pane
:link: capture-pane
:link-type: ref
Read visible content of a pane.
:::

:::{grid-item-card} get_pane_info
:link: get-pane-info
:link-type: ref
Get detailed pane metadata.
:::

:::{grid-item-card} search_panes
:link: search-panes
:link-type: ref
Search text across panes.
:::

:::{grid-item-card} wait_for_text
:link: wait-for-text
:link-type: ref
Wait for text to appear in a pane.
:::

:::{grid-item-card} snapshot_pane
:link: snapshot-pane
:link-type: ref
Rich capture: content + cursor + mode + scroll.
:::

:::{grid-item-card} wait_for_content_change
:link: wait-for-content-change
:link-type: ref
Wait for any screen change.
:::

:::{grid-item-card} display_message
:link: display-message
:link-type: ref
Query arbitrary tmux format strings.
:::

::::

## Act

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} send_keys
:link: send-keys
:link-type: ref
Send commands or keystrokes to a pane.
:::

:::{grid-item-card} set_pane_title
:link: set-pane-title
:link-type: ref
Set pane title.
:::

:::{grid-item-card} clear_pane
:link: clear-pane
:link-type: ref
Clear pane content.
:::

:::{grid-item-card} resize_pane
:link: resize-pane
:link-type: ref
Adjust pane dimensions.
:::

:::{grid-item-card} select_pane
:link: select-pane
:link-type: ref
Focus a pane by ID or direction.
:::

:::{grid-item-card} swap_pane
:link: swap-pane
:link-type: ref
Exchange positions of two panes.
:::

:::{grid-item-card} pipe_pane
:link: pipe-pane
:link-type: ref
Stream pane output to a file.
:::

:::{grid-item-card} enter_copy_mode
:link: enter-copy-mode
:link-type: ref
Enter copy mode for scrollback.
:::

:::{grid-item-card} exit_copy_mode
:link: exit-copy-mode
:link-type: ref
Exit copy mode.
:::

:::{grid-item-card} paste_text
:link: paste-text
:link-type: ref
Paste multi-line text via tmux buffer.
:::

::::

## Destroy

::::{grid} 1 2 3 3
:gutter: 2 2 3 3

:::{grid-item-card} kill_pane
:link: kill-pane
:link-type: ref
Destroy a pane.
:::

::::

```{toctree}
:hidden:

capture-pane
get-pane-info
search-panes
wait-for-text
snapshot-pane
wait-for-content-change
display-message
send-keys
set-pane-title
clear-pane
resize-pane
select-pane
swap-pane
pipe-pane
enter-copy-mode
exit-copy-mode
paste-text
kill-pane
```
