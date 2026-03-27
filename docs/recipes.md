(recipes)=

# Recipes

Composable workflow patterns showing how to combine tools. Each recipe shows the tool sequence, why each step matters, and a common mistake to avoid.

## Run a command and capture output

The fundamental workflow. Most agent interactions follow this pattern.

```json
{"tool": "send_keys", "arguments": {"keys": "make test", "pane_id": "%0"}}
```

```json
{"tool": "wait_for_text", "arguments": {"pattern": "\\$\\s*$", "pane_id": "%0", "regex": true}}
```

```json
{"tool": "capture_pane", "arguments": {"pane_id": "%0", "start": -50}}
```

**Why each step matters:**
- `send_keys` sends the command but returns immediately — it does not wait for completion
- `wait_for_text` blocks until the shell prompt returns (the `$` regex), confirming the command finished
- `capture_pane` reads the result after the command has completed

```{warning}
Do not call `capture_pane` immediately after `send_keys`. There is a race condition — you may capture the terminal *before* the command produces output. Always use `wait_for_text` between them.
```

## Start a service and wait for readiness

Use when you need a background service running before proceeding — web servers, databases, build watchers.

```json
{"tool": "split_window", "arguments": {"session_name": "dev", "direction": "right"}}
```

The new pane's `pane_id` is in the response. Use it for the remaining steps:

```json
{"tool": "send_keys", "arguments": {"keys": "npm run dev", "pane_id": "%1"}}
```

```json
{"tool": "wait_for_text", "arguments": {"pattern": "Local:.*http://localhost", "pane_id": "%1", "regex": true, "timeout": 30}}
```

Now the server is ready — run tests in the original pane:

```json
{"tool": "send_keys", "arguments": {"keys": "npx playwright test", "pane_id": "%0"}}
```

**Common mistake:** Using a fixed `sleep` instead of `wait_for_text`. Server startup times vary — `wait_for_text` adapts automatically.

## Search for errors across all panes

Find which pane has an error without knowing where to look.

```json
{"tool": "search_panes", "arguments": {"pattern": "error", "session_name": "dev"}}
```

The response lists every pane with matching lines. Then capture the full context from each match:

```json
{"tool": "capture_pane", "arguments": {"pane_id": "%2", "start": -100}}
```

**Why two steps:** `search_panes` is fast — it uses tmux's built-in filter for plain text patterns and never captures full pane content. Once you know *which* pane has the error, `capture_pane` gets the full context.

**Common mistake:** Using `list_panes` to find errors. `list_panes` only searches metadata (names, IDs, current command) — not terminal content.

## Set up a multi-pane workspace

Create a structured development layout with labeled panes.

```json
{"tool": "create_session", "arguments": {"session_name": "workspace"}}
```

```json
{"tool": "split_window", "arguments": {"session_name": "workspace", "direction": "right"}}
```

```json
{"tool": "split_window", "arguments": {"session_name": "workspace", "direction": "below"}}
```

```json
{"tool": "select_layout", "arguments": {"session_name": "workspace", "layout": "main-vertical"}}
```

Label each pane for later identification:

```json
{"tool": "set_pane_title", "arguments": {"pane_id": "%0", "title": "editor"}}
```

```json
{"tool": "set_pane_title", "arguments": {"pane_id": "%1", "title": "server"}}
```

```json
{"tool": "set_pane_title", "arguments": {"pane_id": "%2", "title": "tests"}}
```

Then start processes in each:

```json
{"tool": "send_keys", "arguments": {"keys": "vim .", "pane_id": "%0"}}
```

```json
{"tool": "send_keys", "arguments": {"keys": "npm run dev", "pane_id": "%1"}}
```

## Interrupt a hung command

Recover when a command is stuck or waiting for input.

```json
{"tool": "send_keys", "arguments": {"keys": "C-c", "pane_id": "%0", "enter": false}}
```

```json
{"tool": "wait_for_text", "arguments": {"pattern": "\\$\\s*$", "pane_id": "%0", "regex": true, "timeout": 5}}
```

**Why `enter: false`:** Ctrl-C is a tmux key name, not text to type. Setting `enter: false` prevents sending an extra Enter keystroke after the interrupt signal.

**If the interrupt fails** (process ignores Ctrl-C), use `kill_pane` to destroy the pane and `split_window` to get a fresh one.

## Clean capture (no prior output)

Get a clean capture without output from previous commands.

```json
{"tool": "clear_pane", "arguments": {"pane_id": "%0"}}
```

```json
{"tool": "send_keys", "arguments": {"keys": "pytest -x", "pane_id": "%0"}}
```

```json
{"tool": "wait_for_text", "arguments": {"pattern": "passed|failed|error", "pane_id": "%0", "regex": true, "timeout": 60}}
```

```json
{"tool": "capture_pane", "arguments": {"pane_id": "%0"}}
```

**Why clear first:** Without clearing, `capture_pane` returns the visible viewport which may include output from prior commands. Clearing ensures you only capture output from the command you just ran.
