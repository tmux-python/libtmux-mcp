# List program runs

```{fastmcp-tool} program_tools.list_program_runs
```

Read compact local summaries without querying tmux. Follow `next_cursor` until
it is `null`; each page defaults to four items and accepts at most sixteen.
Deleting a previous page's record does not invalidate its cursor. Pages reflect
current state, so newly created records may appear later and completed records
can disappear. Start again without a cursor to refresh the whole view.

Use {tool}`get-program-run` for one owner's cleanup errors and prior exit or
capture evidence. A record marked `running` or `cleanup_in_progress` is still
settling. Retry {tool}`cleanup-program` only for `cleanup_required` records.
Successful runs leave no record. Pagination never evicts an unresolved owner.

```{fastmcp-tool-input} program_tools.list_program_runs
```
