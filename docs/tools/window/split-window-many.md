# Split window many

Adds several panes to a window in one call and keeps a layout applied after
every split. Splitting halves a pane, so a window runs out of room after a few
{tooliconl}`split-window` calls with "no space for new pane"; applying a layout
once at the end does not help, because the failure comes first.

```python
panes = split_window_many(window_id="@1", count=6, layout="tiled")
```

```{fastmcp-tool} window_tools.split_window_many
```

**Use when** you want one pane per worker, per host or per log.

**Avoid when** you need a specific split direction or size for one pane. Use
{tooliconl}`split-window`.

**Side effects:** Creates `count` panes and changes the window's layout. The
window keeps the layout afterwards. When the window is too small for another
pane even after the layout is applied, the call fails and the panes created so
far remain.

`environment`, `start_directory` and `suppress_persistent_history` apply to
every new pane exactly as for {tooliconl}`split-window`; see {ref}`trust` for
what environment values expose.

```{fastmcp-tool-input} window_tools.split_window_many
```
