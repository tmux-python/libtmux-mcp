# Pipe pane

```{fastmcp-tool} pane_tools.pipe_pane
```

**Use when** you need to log pane output to a file — useful for monitoring
long-running processes or capturing output that scrolls past the visible
area.

**Avoid when** you only need a one-time capture — use {tooliconl}`capture-pane`
with `start`/`end` to read scrollback.

**Side effects:** Starts or stops piping output to a file. Call with
`output_path=null` to stop.

**Example:**

```json
{
  "tool": "pipe_pane",
  "arguments": {
    "pane_id": "%0",
    "output_path": "/tmp/build.log"
  }
}
```

Response (start):

```text
Piping pane %0 to /tmp/build.log
```

**Stopping the pipe:**

```json
{
  "tool": "pipe_pane",
  "arguments": {
    "pane_id": "%0",
    "output_path": null
  }
}
```

Response (stop):

```text
Piping stopped for pane %0
```

```{fastmcp-tool-input} pane_tools.pipe_pane
```
