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

`suppress_persistent_history` defaults to `false` for MCP and direct Python calls. It does not inherit {envvar}`LIBTMUX_SUPPRESS_HISTORY`. Leave it `false` to add no history controls for this call. That choice cannot remove inherited, session, or startup-file controls.

Set it to `true` and {tooliconl}`respawn-pane` copies and merges best-effort no-disk history controls for only the spawned process. It does not change the tmux session environment or affect later panes. The shell can retain in-memory history, and a startup file can override these controls after the process starts.

The history policy does not rewrite command text. The `shell` text is passed through unchanged. If you also pass `environment`, any history-control values must agree with the policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression. See {ref}`history-hygiene` for shell behavior and {ref}`safety` for output, scrollback, process, transcript, hook, and logging boundaries.

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

Mapping input keeps the keys visible in the audit log but replaces each `environment` *value* with a `{len, sha256_prefix}` digest. A JSON object string is redacted as one scalar digest, so its keys are not retained in the audit record. Values may still appear briefly in the OS process table while tmux spawns the new process — see {ref}`safety` for details.

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
