# Evaluate tmux format string (display_message)

```{fastmcp-tool} pane_tools.display_message
```

**Use when** you need to query arbitrary tmux variables — zoom state, pane
dead flag, client activity, or any `#{format}` string that isn't covered by
other tools. Despite the historical name (`display_message` is the tmux verb
it wraps), this tool does **not** display anything to the user; it expands
the format string with `display-message -p` and returns the value.

**Avoid when** a dedicated tool already provides the information — e.g. use
{tooliconl}`snapshot-pane` for cursor position and mode, or
{tooliconl}`get-pane-info` for standard metadata.

**Side effects:** None. Readonly.

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
