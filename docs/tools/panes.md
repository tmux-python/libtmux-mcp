# Panes

## Inspect

```{fastmcp-tool} pane_tools.capture_pane
```

**Use when** you need to read what's currently displayed in a terminal —
after running a command, checking output, or verifying state.

**Avoid when** you need to search across multiple panes at once — use
{tooliconl}`search-panes`. If you only need pane metadata (not content), use
{tooliconl}`get-pane-info`.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "capture_pane",
  "arguments": {
    "pane_id": "%0",
    "start": -50
  }
}
```

Response (string):

```text
$ echo "Running tests..."
Running tests...
$ echo "PASS: test_auth (0.3s)"
PASS: test_auth (0.3s)
$ echo "FAIL: test_upload (AssertionError)"
FAIL: test_upload (AssertionError)
$ echo "3 tests: 2 passed, 1 failed"
3 tests: 2 passed, 1 failed
$
```

```{fastmcp-tool-input} pane_tools.capture_pane
```

---

```{fastmcp-tool} pane_tools.get_pane_info
```

**Use when** you need pane dimensions, PID, current working directory, or
other metadata without reading the terminal content.

**Avoid when** you need the actual text — use {tooliconl}`capture-pane`.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "get_pane_info",
  "arguments": {
    "pane_id": "%0"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "0",
  "pane_width": "80",
  "pane_height": "24",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.get_pane_info
```

---

```{fastmcp-tool} pane_tools.search_panes
```

**Use when** you need to find specific text across multiple panes — locating
which pane has an error, finding a running process, or checking output
without knowing which pane to look in.

**Avoid when** you already know the target pane — use {tooliconl}`capture-pane`
directly.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "search_panes",
  "arguments": {
    "pattern": "FAIL",
    "session_name": "dev"
  }
}
```

Response:

```json
[
  {
    "pane_id": "%0",
    "pane_current_command": "zsh",
    "pane_current_path": "/home/user/myproject",
    "window_id": "@0",
    "window_name": "editor",
    "session_id": "$0",
    "session_name": "dev",
    "matched_lines": [
      "FAIL: test_upload (AssertionError)",
      "3 tests: 2 passed, 1 failed"
    ],
    "is_caller": false
  }
]
```

```{fastmcp-tool-input} pane_tools.search_panes
```

---

```{fastmcp-tool} pane_tools.wait_for_text
```

**Use when** you need to block until specific output appears — waiting for a
server to start, a build to complete, or a prompt to return.

**Avoid when** the expected text may never appear — always set a reasonable
`timeout`. For known output, {tooliconl}`capture-pane` after a known delay
may suffice, but `wait_for_text` is preferred because it adapts to variable
timing.

**Side effects:** None. Readonly. Blocks until text appears or timeout.

**Example:**

```json
{
  "tool": "wait_for_text",
  "arguments": {
    "pattern": "Server listening",
    "pane_id": "%2",
    "timeout": 30
  }
}
```

Response:

```json
{
  "found": true,
  "matched_lines": [
    "Server listening on port 8000"
  ],
  "pane_id": "%2",
  "elapsed_seconds": 0.002,
  "timed_out": false
}
```

```{fastmcp-tool-input} pane_tools.wait_for_text
```

---

```{fastmcp-tool} pane_tools.snapshot_pane
```

**Use when** you need a complete picture of a pane in a single call — visible
text plus cursor position, whether the pane is in copy mode, scroll offset,
and scrollback history size. Replaces separate `capture_pane` +
`get_pane_info` calls when you need to reason about cursor location or
terminal mode.

**Avoid when** you only need raw text — {tooliconl}`capture-pane` is lighter.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "snapshot_pane",
  "arguments": {
    "pane_id": "%0"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "content": "$ npm test\n\nPASS src/auth.test.ts\nTests: 3 passed\n$",
  "cursor_x": 2,
  "cursor_y": 4,
  "pane_width": 80,
  "pane_height": 24,
  "pane_in_mode": false,
  "pane_mode": null,
  "scroll_position": null,
  "history_size": 142,
  "title": null,
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.snapshot_pane
```

---

```{fastmcp-tool} pane_tools.wait_for_content_change
```

**Use when** you've sent a command and need to wait for *something* to happen,
but you don't know what the output will look like. Unlike
{tooliconl}`wait-for-text`, this waits for *any* screen change rather than a
specific pattern.

**Avoid when** you know the expected output — {tooliconl}`wait-for-text` is more
precise and avoids false positives from unrelated output.

**Side effects:** None. Readonly. Blocks until content changes or timeout.

**Example:**

```json
{
  "tool": "wait_for_content_change",
  "arguments": {
    "pane_id": "%0",
    "timeout": 10
  }
}
```

Response:

```json
{
  "changed": true,
  "pane_id": "%0",
  "elapsed_seconds": 1.234,
  "timed_out": false
}
```

```{fastmcp-tool-input} pane_tools.wait_for_content_change
```

---

```{fastmcp-tool} pane_tools.display_message
```

**Use when** you need to query arbitrary tmux variables — zoom state, pane
dead flag, client activity, or any `#{format}` string that isn't covered by
other tools.

**Avoid when** a dedicated tool already provides the information — e.g. use
{tooliconl}`snapshot-pane` for cursor position and mode, or
{tooliconl}`get-pane-info` for standard metadata.

**Side effects:** None. Readonly.

**Example:**

```json
{
  "tool": "display_message",
  "arguments": {
    "format_string": "zoomed=#{window_zoomed_flag} dead=#{pane_dead}",
    "pane_id": "%0"
  }
}
```

Response (string):

```text
zoomed=0 dead=0
```

```{fastmcp-tool-input} pane_tools.display_message
```

## Act

```{fastmcp-tool} pane_tools.send_keys
```

**Use when** you need to type commands, press keys, or interact with a
terminal. This is the primary way to execute commands in tmux panes.

**Avoid when** you need to run something and immediately capture the result —
send keys first, then use {tooliconl}`capture-pane` or {tooliconl}`wait-for-text`.

**Side effects:** Sends keystrokes to the pane. If `enter` is true (default),
the command executes.

**Example:**

```json
{
  "tool": "send_keys",
  "arguments": {
    "keys": "npm start",
    "pane_id": "%2"
  }
}
```

Response (string):

```text
Keys sent to pane %2
```

```{fastmcp-tool-input} pane_tools.send_keys
```

---

```{fastmcp-tool} pane_tools.set_pane_title
```

**Use when** you want to label a pane for identification.

**Side effects:** Changes the pane title.

**Example:**

```json
{
  "tool": "set_pane_title",
  "arguments": {
    "pane_id": "%0",
    "title": "build"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "0",
  "pane_width": "80",
  "pane_height": "24",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "build",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.set_pane_title
```

---

```{fastmcp-tool} pane_tools.clear_pane
```

**Use when** you want a clean terminal before capturing output.

**Side effects:** Clears the pane's visible content.

**Example:**

```json
{
  "tool": "clear_pane",
  "arguments": {
    "pane_id": "%0"
  }
}
```

Response (string):

```text
Pane cleared: %0
```

```{fastmcp-tool-input} pane_tools.clear_pane
```

---

```{fastmcp-tool} pane_tools.resize_pane
```

**Use when** you need to adjust pane dimensions.

**Side effects:** Changes pane size. May affect adjacent panes.

**Example:**

```json
{
  "tool": "resize_pane",
  "arguments": {
    "pane_id": "%0",
    "height": 15
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "0",
  "pane_width": "80",
  "pane_height": "15",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.resize_pane
```

---

```{fastmcp-tool} pane_tools.select_pane
```

**Use when** you need to focus a specific pane — by ID for a known target,
or by direction (`up`, `down`, `left`, `right`, `last`, `next`, `previous`)
to navigate a multi-pane layout.

**Side effects:** Changes the active pane in the window.

**Example:**

```json
{
  "tool": "select_pane",
  "arguments": {
    "direction": "down",
    "window_id": "@0"
  }
}
```

Response:

```json
{
  "pane_id": "%1",
  "pane_index": "1",
  "pane_width": "80",
  "pane_height": "11",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12400",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.select_pane
```

---

```{fastmcp-tool} pane_tools.swap_pane
```

**Use when** you want to rearrange pane positions without changing content —
e.g. moving a log pane from bottom to top.

**Side effects:** Exchanges the visual positions of two panes.

**Example:**

```json
{
  "tool": "swap_pane",
  "arguments": {
    "source_pane_id": "%0",
    "target_pane_id": "%1"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "1",
  "pane_width": "80",
  "pane_height": "11",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.swap_pane
```

---

```{fastmcp-tool} pane_tools.pipe_pane
```

**Use when** you need to log pane output to a file — useful for monitoring
long-running processes or capturing output that scrolls past the visible
area.

**Avoid when** you only need a one-time capture — use {tooliconl}`capture-pane`
with `start`/`end` to read scrollback.

**Side effects:** Starts or stops piping output to a file. Call with
`output_path=null` to stop.

**Example:**

```json
{
  "tool": "pipe_pane",
  "arguments": {
    "pane_id": "%0",
    "output_path": "/tmp/build.log"
  }
}
```

Response (start):

```text
Piping pane %0 to /tmp/build.log
```

**Stopping the pipe:**

```json
{
  "tool": "pipe_pane",
  "arguments": {
    "pane_id": "%0",
    "output_path": null
  }
}
```

Response (stop):

```text
Piping stopped for pane %0
```

```{fastmcp-tool-input} pane_tools.pipe_pane
```

---

```{fastmcp-tool} pane_tools.enter_copy_mode
```

**Use when** you need to scroll through scrollback history in a pane.
Optionally scroll up immediately after entering. Use
{tooliconl}`snapshot-pane` afterward to read the `scroll_position` and
visible content.

**Side effects:** Puts the pane into copy mode. The pane stops receiving
new output until you exit copy mode.

**Example:**

```json
{
  "tool": "enter_copy_mode",
  "arguments": {
    "pane_id": "%0",
    "scroll_up": 50
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "0",
  "pane_width": "80",
  "pane_height": "24",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.enter_copy_mode
```

---

```{fastmcp-tool} pane_tools.exit_copy_mode
```

**Use when** you're done scrolling through scrollback and want the pane to
resume receiving output.

**Side effects:** Exits copy mode, returning the pane to normal.

**Example:**

```json
{
  "tool": "exit_copy_mode",
  "arguments": {
    "pane_id": "%0"
  }
}
```

Response:

```json
{
  "pane_id": "%0",
  "pane_index": "0",
  "pane_width": "80",
  "pane_height": "24",
  "pane_current_command": "zsh",
  "pane_current_path": "/home/user/myproject",
  "pane_pid": "12345",
  "pane_title": "",
  "pane_active": "1",
  "window_id": "@0",
  "session_id": "$0",
  "is_caller": false
}
```

```{fastmcp-tool-input} pane_tools.exit_copy_mode
```

---

```{fastmcp-tool} pane_tools.paste_text
```

**Use when** you need to paste multi-line text into a pane — e.g. a code
block, a config snippet, or a heredoc. Uses tmux paste buffers for clean
multi-line input instead of sending text line-by-line via
{tooliconl}`send-keys`.

**Side effects:** Pastes text into the pane. With `bracket=true` (default),
uses bracketed paste mode so the terminal knows this is pasted text.

**Example:**

```json
{
  "tool": "paste_text",
  "arguments": {
    "text": "def hello():\n    print('world')\n",
    "pane_id": "%0"
  }
}
```

Response (string):

```text
Text pasted to pane %0
```

```{fastmcp-tool-input} pane_tools.paste_text
```

## Destroy

```{fastmcp-tool} pane_tools.kill_pane
```

**Use when** you're done with a specific terminal and want to remove it
without affecting sibling panes.

**Avoid when** you want to remove the entire window — use {tooliconl}`kill-window`.

**Side effects:** Destroys the pane. Not reversible.

**Example:**

```json
{
  "tool": "kill_pane",
  "arguments": {
    "pane_id": "%1"
  }
}
```

Response (string):

```text
Pane killed: %1
```

```{fastmcp-tool-input} pane_tools.kill_pane
```
