# Show environment

```{fastmcp-tool} env_tools.show_environment
```

**Use when** you need to inspect tmux environment variables.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "show_environment",
  "arguments": {}
}
```

Response:

```json
{
  "variables": {
    "SHELL": "/bin/zsh",
    "TERM": "xterm-256color",
    "HOME": "/home/user",
    "USER": "user",
    "LANG": "C.UTF-8"
  }
}
```

```{fastmcp-tool-input} env_tools.show_environment
```

## Act
