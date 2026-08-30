# Show hooks

```{fastmcp-tool} hook_tools.show_hooks
```

**Use when** you need to enumerate every hook configured on a
target — the human user's tmux config, an inherited team setup, or
a session that another tool may have touched.

**Side effects:** The built-in request reads data only. A configured alias or
hook can add effects; see {ref}`trust`.

```{fastmcp-tool-input} hook_tools.show_hooks
```
