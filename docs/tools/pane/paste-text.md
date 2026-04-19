# Paste text

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
