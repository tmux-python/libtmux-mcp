```{fastmcp-tool} env_tools.set_environment
```

**Use when** you need to set a tmux environment variable.

**Side effects:** Sets the variable in the tmux server.

**Example:**

```json
{
  "tool": "set_environment",
  "arguments": {
    "name": "MY_VAR",
    "value": "hello"
  }
}
```

Response:

```json
{
  "name": "MY_VAR",
  "value": "hello",
  "status": "set"
}
```

```{fastmcp-tool-input} env_tools.set_environment
```
