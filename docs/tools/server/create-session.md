# Create session

```{fastmcp-tool} server_tools.create_session
```

**Use when** you need a new isolated workspace. Sessions are the top-level
container — create one before creating windows or panes.

**Avoid when** a session with the target name already exists — check with
{tooliconl}`list-sessions` first, or the command will fail.

**Side effects:** Creates a new tmux session with one window and one pane.

**Do not pass credentials directly in `environment`.** Values persist in the
new session, can be inspected with {tooliconl}`show-environment`, and reach
its initial pane and future panes. Pass credential references instead; see
{ref}`safety` for details.

`suppress_persistent_history` defaults to `false` for MCP and direct Python calls. It does not inherit {envvar}`LIBTMUX_SUPPRESS_HISTORY`. Leave it `false` to add no history controls for this call. That choice cannot remove inherited, session, or startup-file controls.

Set it to `true` and {tooliconl}`create-session` copies and merges best-effort no-disk history controls into the tmux session environment. They reach the initial pane and future panes in that session. The shell can retain in-memory history, and a startup file can override these controls after the process starts.

When you enable it, tmux environment arguments are added, but the spawned process command text is not prefixed or rewritten. If you also pass `environment`, any history-control values must agree with the policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression. See {ref}`history-hygiene` for shell behavior and {ref}`safety` for output, scrollback, process, transcript, hook, and logging boundaries.

**Example:**

```json
{
  "tool": "create_session",
  "arguments": {
    "session_name": "dev"
  }
}
```

Response ({class}`~libtmux_mcp.models.SessionInfo`):

```json
{
  "session_id": "$1",
  "session_name": "dev",
  "window_count": 1,
  "session_attached": "0",
  "session_created": "1774521872",
  "active_pane_id": "%0"
}
```

```{tip}
The returned ``active_pane_id`` is the pane ID (``%N``) of the
session's initial pane. It's guaranteed non-``None`` immediately
after {toolref}`create-session` (the
{external+libtmux:doc}`libtmux <index>` layer always creates the
session with one initial pane), so you can target subsequent
{toolref}`send-keys` / {toolref}`split-window` /
{toolref}`capture-pane` calls directly without a
follow-up {tooliconl}`list-panes` round-trip — saving an MCP call
in the most common "new session, then act on it" workflow.
```

```{fastmcp-tool-input} server_tools.create_session
```
