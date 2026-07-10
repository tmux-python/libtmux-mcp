# Respawn pane

```{fastmcp-tool} pane_tools.respawn_pane
```

**Use when** a pane's shell or command has wedged (hung REPL, runaway
process, bad terminal mode) and you need a clean restart *without*
destroying the `pane_id` references other tools or callers may still
be holding. With `kill=True` (the default) tmux kills the current
process first; optional `shell` relaunches with a different command;
optional `start_directory` sets its cwd; optional `environment` adds
per-process env vars (one `-e KEY=VALUE` flag per entry).

**Avoid when** the pane genuinely needs to go away — use
{tooliconl}`kill-pane` instead. Also avoid when you want to change
the layout: `respawn-pane` preserves the pane in place.

**Side effects:** Kills the current process (with `kill=True`) and
starts a new one. **The `pane_id` is preserved** — that's the whole
point of the tool. `pane_pid` updates to the new process.

For MCP calls, an omitted `suppress_history` follows the startup default in {ref}`configuration`, and an explicit `true` or `false` wins. Direct Python calls default to `False`. When suppression is effective, {tooliconl}`respawn-pane` adds an environment that applies to only the spawned process; it does not change the tmux session environment or affect later panes. An explicit `false` prevents new controls but does not remove controls inherited by the pane process. Shell startup files can override the controls; see {ref}`history-hygiene` and {ref}`safety`.

The history policy only copies and merges environment values; it does not rewrite command text. The `shell` text is passed through unchanged. If you also pass `environment`, any history-control values must agree with the suppression policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression.

**Tip:** Call {tooliconl}`get-pane-info` first if you need to capture
`pane_current_command` before respawn — the new process loses its argv.
Omitting `shell` makes tmux replay the original argv (good default for
shells; may differ for processes spawned via custom shell at split
time).

**Example — recover a wedged pane, relaunching the default shell:**

```json
{
  "tool": "respawn_pane",
  "arguments": {
    "pane_id": "%5"
  }
}
```

**Example — relaunch with a different command and working directory:**

```json
{
  "tool": "respawn_pane",
  "arguments": {
    "pane_id": "%5",
    "shell": "pytest -x",
    "start_directory": "/home/user/project"
  }
}
```

**Example — relaunch with extra environment variables:**

```json
{
  "tool": "respawn_pane",
  "arguments": {
    "pane_id": "%5",
    "shell": "pytest -x",
    "environment": {
      "PYTHONPATH": "/home/user/project/src",
      "DATABASE_URL": "postgres://localhost/test"
    }
  }
}
```

The audit log redacts each `environment` *value* via `{len, sha256_prefix}` digests but keeps the keys visible (env var names like `DATABASE_URL` are operator-debug-useful, while their values are the secret). Note that values may still appear briefly in the OS process table while tmux spawns the new process — see {ref}`safety` for details.

Response (PaneInfo):

```json5
{
  "pane_id": "%5",
  "pane_pid": "98765",
  "pane_current_command": "pytest",
  "pane_current_path": "/home/user/project",
  // ...
}
```

```{fastmcp-tool-input} pane_tools.respawn_pane
```
