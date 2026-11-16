# Get server capabilities

```{fastmcp-tool} server_tools.get_server_capabilities
```

The report includes the observed daemon PID, start time, version and each
library feature's minimum release, status and reason. It reads the selected
daemon afresh and does not start one. Unknown version strings remain unknown.
Operations recheck their own requirements; a saved report cannot authorize a
later operation after the daemon restarts.

MCP toolsets still determine the tools advertised by `tools/list`.

```{fastmcp-tool-input} server_tools.get_server_capabilities
```
