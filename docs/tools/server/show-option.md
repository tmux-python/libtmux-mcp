# Show option

```{fastmcp-tool} option_tools.show_option
```

**Use when** you need to check a tmux configuration value — buffer limits,
history size, status bar settings, etc.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "show_option",
  "arguments": {
    "option": "history-limit"
  }
}
```

Response:

```json
{
  "option": "history-limit",
  "value": "2000"
}
```

```{fastmcp-tool-input} option_tools.show_option
```
