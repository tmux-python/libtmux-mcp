# Get program run

```{fastmcp-tool} program_tools.get_program_run
```

Read one owner's bounded error summaries and any exit or capture obtained before
a failure. The record contains the original route and public cleanup disposition.
The capture's truncation flag and unknown history completeness remain explicit.
Use {tool}`cleanup-program` when its status is `cleanup_required`.

```{fastmcp-tool-input} program_tools.get_program_run
```
