# Clean up program

```{fastmcp-tool} program_tools.cleanup_program
```

Pass the `run_id` from {tool}`list-program-runs` or the `program_recovery_id`
from error metadata. Cleanup uses the retained owner; it does not reconstruct
ownership from a pane ID. Failed cleanup keeps the record for retry.
If termination fails, cleanup still checks whether the owned pane is absent.
A verified absence retires the record; otherwise both failures remain available
in its cleanup details.

The tool belongs to `execute` so an operator who permits owned execution can
also recover its resources. It can terminate that owner's remaining program.

```{fastmcp-tool-input} program_tools.cleanup_program
```
