# Create session

```{fastmcp-tool} server_tools.create_session
```

**Use when** you need a new isolated workspace. Sessions are the top-level
container — create one before creating windows or panes.

**Avoid when** a session with the target name already exists — check with
{tooliconl}`list-sessions` first, or the command will fail.

**Side effects:** Creates a new tmux session with one window and one pane.

For MCP calls, an omitted `suppress_history` follows the startup default in {ref}`configuration`, and an explicit `true` or `false` wins. Direct Python calls default to `False`. When suppression is effective, {tooliconl}`create-session` copies and merges supported shell-history controls into the tmux session environment, so they reach the initial pane and future panes in that session. An explicit `false` prevents new controls but does not remove compatible values already supplied in `environment`. Shell startup files can override the controls, and suppression does not remove terminal output or other traces; see {ref}`history-hygiene` and {ref}`safety`.

The history policy only copies and merges environment values; it does not rewrite command text or tmux launch arguments. If you also pass `environment`, any history-control values must agree with the suppression policy. A conflict fails the call, names the variable without including the conflicting value, and is never retried without suppression.

**Example:**

```json
{
  "tool": "create_session",
  "arguments": {
    "session_name": "dev"
  }
}
```

Response:

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
