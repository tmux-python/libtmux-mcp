# Clean up program

```{fastmcp-tool} program_tools.cleanup_program
```

Pass the `run_id` from {tool}`list-program-runs` or the `program_recovery_id`
from error metadata. Cleanup uses the retained owner; it does not reconstruct
ownership from a pane ID. Failed cleanup keeps the record for retry.

The tool belongs to `execute` so an operator who permits owned execution can
also recover its resources. It can terminate that owner's remaining program.

```{fastmcp-tool-input} program_tools.cleanup_program
```
