# Run program

```{fastmcp-tool} program_tools.run_program
```

Pass an executable and at least one argument. No command shell expands the
arguments; explicitly invoke a shell when the application needs one.

```json
{
  "tool": "run_program",
  "arguments": {
    "session_id": "$0",
    "argv": ["python3", "-c", "print('finished'); raise SystemExit(7)"],
    "timeout": 10,
    "output_bytes": 8192
  }
}
```

A nonzero application exit is a successful tool result with that exit status.
An operation or cleanup failure is a tool error; its metadata preserves
available exit and capture evidence and identifies any retained cleanup owner.
See {doc}`index` for cancellation, output bounds and recovery.

```{fastmcp-tool-input} program_tools.run_program
```
