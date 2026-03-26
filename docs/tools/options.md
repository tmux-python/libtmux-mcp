# Options & Environment

## Inspect

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

---

```{fastmcp-tool} env_tools.show_environment
```

**Use when** you need to inspect tmux environment variables.

**Side effects:** None. Readonly.

```{fastmcp-tool-input} env_tools.show_environment
```

## Act

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

---

```{fastmcp-tool} env_tools.set_environment
```

**Use when** you need to set a tmux environment variable.

**Side effects:** Sets the variable in the tmux server.

```{fastmcp-tool-input} env_tools.set_environment
```
