# Clear pane

```{fastmcp-tool} pane_tools.clear_pane
```

**Use when** you want a clean terminal before capturing output.

**Side effects:** Clears the pane's visible content.

**Example:**

```json
{
  "tool": "clear_pane",
  "arguments": {
    "pane_id": "%0"
  }
}
```

Response (string):

```text
Pane cleared: %0
```

```{fastmcp-tool-input} pane_tools.clear_pane
```
