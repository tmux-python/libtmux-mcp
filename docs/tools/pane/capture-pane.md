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
