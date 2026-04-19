# Create session

```{fastmcp-tool} server_tools.create_session
```

**Use when** you need a new isolated workspace. Sessions are the top-level
container — create one before creating windows or panes.

**Avoid when** a session with the target name already exists — check with
{tooliconl}`list-sessions` first, or the command will fail.

**Side effects:** Creates a new tmux session with one window and one pane.

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
after ``create_session`` (libtmux always creates the session with
one initial pane), so you can target subsequent ``send_keys`` /
``split_window`` / ``capture_pane`` calls directly without a
follow-up {tooliconl}`list-panes` round-trip — saving an MCP call
in the most common "new session, then act on it" workflow.
```

```{fastmcp-tool-input} server_tools.create_session
```
