# Respawn pane

```{fastmcp-tool} pane_tools.respawn_pane
```

**Use when** a pane's shell or command has wedged (hung REPL, runaway
process, bad terminal mode) and you need a clean restart *without*
destroying the `pane_id` references other tools or callers may still
be holding. With `kill=True` (the default) tmux kills the current
process first; optional `shell` relaunches with a different command;
optional `start_directory` sets its cwd.

**Avoid when** the pane genuinely needs to go away — use
{tooliconl}`kill-pane` instead. Also avoid when you want to change
the layout: `respawn-pane` preserves the pane in place.

**Side effects:** Kills the current process (with `kill=True`) and
starts a new one. **The `pane_id` is preserved** — that's the whole
point of the tool. `pane_pid` updates to the new process.

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

Response (PaneInfo):

```json
{
  "pane_id": "%5",
  "pane_pid": "98765",
  "pane_current_command": "pytest",
  "pane_current_path": "/home/user/project",
  ...
}
```

```{fastmcp-tool-input} pane_tools.respawn_pane
```
