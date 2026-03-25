# Panes

## Inspect

```{fastmcp-tool} pane_tools.capture_pane
```

**Use when** you need to read what's currently displayed in a terminal —
after running a command, checking output, or verifying state.

**Avoid when** you need to search across multiple panes at once — use
{ref}`search-panes`. If you only need pane metadata (not content), use
{ref}`get-pane-info`.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "capture_pane",
  "arguments": {
    "session_name": "dev",
    "start": -50
  }
}
```

```{fastmcp-tool-input} pane_tools.capture_pane
```

---

```{fastmcp-tool} pane_tools.get_pane_info
```

**Use when** you need pane dimensions, PID, current working directory, or
other metadata without reading the terminal content.

**Avoid when** you need the actual text — use {ref}`capture-pane`.

**Side effects:** None. Readonly.

```{fastmcp-tool-input} pane_tools.get_pane_info
```

---

```{fastmcp-tool} pane_tools.search_panes
```

**Use when** you need to find specific text across multiple panes — locating
which pane has an error, finding a running process, or checking output
without knowing which pane to look in.

**Avoid when** you already know the target pane — use {ref}`capture-pane`
directly.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "search_panes",
  "arguments": {
    "query": "error",
    "session_name": "dev"
  }
}
```

```{fastmcp-tool-input} pane_tools.search_panes
```

---

```{fastmcp-tool} pane_tools.wait_for_text
```

**Use when** you need to block until specific output appears — waiting for a
server to start, a build to complete, or a prompt to return.

**Avoid when** you can poll with {ref}`capture-pane` instead, or if the
expected text may never appear (set a timeout).

**Side effects:** None. Readonly. Blocks until text appears or timeout.

**Example:**

```json
{
  "tool": "wait_for_text",
  "arguments": {
    "text": "Server started",
    "session_name": "dev",
    "timeout": 30
  }
}
```

```{fastmcp-tool-input} pane_tools.wait_for_text
```

## Act

```{fastmcp-tool} pane_tools.send_keys
```

**Use when** you need to type commands, press keys, or interact with a
terminal. This is the primary way to execute commands in tmux panes.

**Avoid when** you need to run something and immediately capture the result —
send keys first, then use {ref}`capture-pane` or {ref}`wait-for-text`.

**Side effects:** Sends keystrokes to the pane. If `enter` is true (default),
the command executes.

**Example:**

```json
{
  "tool": "send_keys",
  "arguments": {
    "keys": "npm start",
    "session_name": "dev"
  }
}
```

```{fastmcp-tool-input} pane_tools.send_keys
```

---

```{fastmcp-tool} pane_tools.set_pane_title
```

**Use when** you want to label a pane for identification.

**Side effects:** Changes the pane title.

```{fastmcp-tool-input} pane_tools.set_pane_title
```

---

```{fastmcp-tool} pane_tools.clear_pane
```

**Use when** you want a clean terminal before capturing output.

**Side effects:** Clears the pane's visible content.

```{fastmcp-tool-input} pane_tools.clear_pane
```

---

```{fastmcp-tool} pane_tools.resize_pane
```

**Use when** you need to adjust pane dimensions.

**Side effects:** Changes pane size. May affect adjacent panes.

```{fastmcp-tool-input} pane_tools.resize_pane
```

## Destroy

```{fastmcp-tool} pane_tools.kill_pane
```

**Use when** you're done with a specific terminal and want to remove it
without affecting sibling panes.

**Avoid when** you want to remove the entire window — use {ref}`kill-window`.

**Side effects:** Destroys the pane. Not reversible.

```{fastmcp-tool-input} pane_tools.kill_pane
```
