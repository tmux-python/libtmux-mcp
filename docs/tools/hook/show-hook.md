# Show hook

```{fastmcp-tool} hook_tools.show_hook
```

**Use when** you know which hook you want to inspect by name. Returns
empty when the hook is unset; raises `ToolError` for unknown hook
names (typos, wrong scope) so input mistakes don't masquerade as
"nothing configured".

**Side effects:** None. Readonly.

```{fastmcp-tool-input} hook_tools.show_hook
```
