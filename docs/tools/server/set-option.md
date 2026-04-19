# Set option

```{fastmcp-tool} option_tools.set_option
```

**Use when** you need to change tmux behavior — adjusting history limits,
enabling mouse support, changing status bar format.

**Side effects:** Changes the tmux option value.

**Example:**

```json
{
  "tool": "set_option",
  "arguments": {
    "option": "history-limit",
    "value": "50000"
  }
}
```

Response:

```json
{
  "option": "history-limit",
  "value": "50000",
  "status": "set"
}
```

```{fastmcp-tool-input} option_tools.set_option
```
