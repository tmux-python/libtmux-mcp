# Read tmux variables (display_message)

```{fastmcp-tool} pane_tools.display_message
```

**Use when** you need to read a tmux variable no dedicated tool covers —
zoom state, pane dead flag, client activity. Despite the historical name
(`display_message` is the tmux verb it wraps), this tool does **not** display
anything to the user; it substitutes the variables and returns the value.

**Avoid when** a dedicated tool already provides the information — e.g. use
{tooliconl}`snapshot-pane` for cursor position and mode,
{tooliconl}`get-pane-info` for standard metadata (including the new
`pane_left` / `pane_top` / `pane_at_*` geometry block), or
{tooliconl}`find-pane-by-position` to resolve a window corner to a
{class}`~libtmux_mcp.models.PaneInfo` without parsing `#{pane_at_bottom}` / `#{pane_at_right}`
yourself.

**Side effects:** None. Reads only.

Accepts literal text and `#{variable}` references only. Modifiers such as
`#{E:...}` re-expand a variable's *value*, which can arrive from a pane
rather than from you, so they are refused rather than filtered.

Modifiers, conditionals, and padding are therefore no longer available here.
Run `tmux display-message -p` through {tooliconl}`run-command` for those: raw
format syntax belongs on the surface labelled execution, where the caller is
the one supplying it.

**Example:**

```json
{
  "tool": "display_message",
  "arguments": {
    "format_string": "zoomed=#{window_zoomed_flag} dead=#{pane_dead}",
    "pane_id": "%0"
  }
}
```

Response (string):

```text
zoomed=0 dead=0
```

```{fastmcp-tool-input} pane_tools.display_message
```
