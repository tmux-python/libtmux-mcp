# Show hook

```{fastmcp-tool} hook_tools.show_hook
```

**Use when** you know which hook you want to inspect by name. Returns
empty when the hook is unset; raises an
{exc}`~libtmux_mcp._utils.ExpectedToolError` for
unknown hook names (typos, wrong scope) so input mistakes don't
masquerade as "nothing configured".

**Side effects:** The built-in request reads data only. A configured alias or
hook can add effects; see {ref}`trust`.

```{fastmcp-tool-input} hook_tools.show_hook
```
