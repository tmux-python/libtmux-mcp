(prompts-overview)=

# Prompts

MCP prompts are reusable, parameterised text templates the server
ships to its clients. A client renders a prompt by calling
``prompts/get``; the rendered text is what the model sees.
libtmux-mcp's prompts are short *workflow recipes* — the MCP-shaped
counterpart to the longer narrative recipes in {doc}`/recipes`.

## Available prompts

::::{grid} 1 2 2 2
:gutter: 2 2 3 3

:::{grid-item-card} `run_and_wait`
:link: fastmcp-prompt-run-and-wait
:link-type: ref
Execute a shell command through {tooliconl}`run-command` and inspect
its typed result.
:::

:::{grid-item-card} `diagnose_failing_pane`
:link: fastmcp-prompt-diagnose-failing-pane
:link-type: ref
Gather pane context and produce a root-cause hypothesis without taking action.
:::

:::{grid-item-card} `build_dev_workspace`
:link: fastmcp-prompt-build-dev-workspace
:link-type: ref
Set up a 3-pane editor / shell / logs layout shell-agnostically.
:::

:::{grid-item-card} `interrupt_gracefully`
:link: fastmcp-prompt-interrupt-gracefully
:link-type: ref
Send SIGINT and verify the shell prompt returns, refusing to auto-escalate.
:::

::::

```{tip}
Most MCP clients render prompts via a slash-command UI
(``/<server>:<prompt>``). For tools-only clients that don't expose
prompts, set ``LIBTMUX_MCP_PROMPTS_AS_TOOLS=1`` in the server
environment to surface them as ``list_prompts`` / ``get_prompt``
tools instead.
```

---

```{fastmcp-prompt} run_and_wait
```

**Use when** the agent needs to execute a single shell command, wait
for completion, and inspect exit status plus output.

**Why use this instead of {tooliconl}`send-keys` + {tooliconl}`capture-pane` polling?**
{tooliconl}`run-command` sends the command, waits through a private
tmux signal, captures tail-preserved output, and returns a
{class}`~libtmux_mcp.models.RunCommandResult`. That removes the manual
channel plumbing from the common authored-command workflow.

```{fastmcp-prompt-input} run_and_wait
```

**Sample render** (``command="pytest"``, ``pane_id="%1"``):

````markdown
Run this shell command in tmux pane %1, wait until it
finishes, and inspect the typed result:

```python
result = run_command(
    pane_id='%1',
    command='pytest',
    timeout=60.0,
    max_lines=100,
)
```

Use `result.exit_status`, `result.timed_out`, and `result.output`
to decide what happened. Do NOT use a `send_keys` + `capture_pane`
retry loop for authored commands — `run_command` already performs
deterministic completion and returns tail-preserved output.

If the task needs persistent shell state or TUI keystrokes instead of
a one-shot shell command, use `send_keys` or `send_keys_batch`, then
observe later output with `capture_since`.
````

Single-line renders omit `suppress_history`, so MCP calls use the server's
enabled-by-default setting. If `command` contains a carriage return or line
feed, the prompt instead renders `suppress_history=False` and warns that the
shell may record the multiline command. See {ref}`history-suppression` for
the Bash, Zsh, and Fish behavior behind both cases.

For custom shell composition that falls outside {tooliconl}`run-command`,
compose ``tmux wait-for -S <channel>`` yourself and call
{tooliconl}`wait-for-channel`. Keep that as the low-level escape hatch,
not the default command-running recipe.

---

```{fastmcp-prompt} diagnose_failing_pane
```

**Use when** something visibly went wrong in a pane and the agent
needs to investigate before deciding what to fix. Produces a plan,
not an action.

**Why use this instead of just calling {tooliconl}`capture-pane`?** The recipe
prefers {tool}`snapshot-pane`, which returns content + cursor
position + pane mode + scroll state in one call — saving a
follow-up {toolref}`get-pane-info` round-trip. It also explicitly forbids
the agent from acting before it has a hypothesis, which prevents
"fix the symptom" anti-patterns. For repeated observation, it routes
follow-up reads through {tool}`capture-since` cursors instead of full
pane captures.

```{fastmcp-prompt-input} diagnose_failing_pane
```

**Sample render** (``pane_id="%1"``):

```markdown
Something went wrong in tmux pane %1. Diagnose it:

1. Call `snapshot_pane(pane_id="%1")` to get content,
   cursor position, pane mode, and scroll state in one call.
2. If the content looks truncated, re-call with `max_lines=None`.
3. If you need to watch the pane across more than one turn, call
   `capture_since(pane_id="%1")`, keep the returned cursor,
   and pass it to later `capture_since(cursor=...)` calls.
4. Identify the last command that ran (look at the prompt line and
   the line above it) and the last non-empty output line.
5. Propose a root cause hypothesis and a minimal command to verify
   it (do NOT execute anything yet — produce the plan first).
```

---

```{fastmcp-prompt} build_dev_workspace
```

**Use when** the operator wants a fresh 3-pane workspace with editor
on top, terminal bottom-left, and a logs pane bottom-right — the
most common shape for active development.

**Why use this instead of describing the layout in free form?** The
recipe uses real parameter names that match the tools' actual
signatures (``session_name=``, ``pane_id=``, ``direction="below"``)
so an agent following it verbatim never hits a validation error. It
also explicitly avoids waiting for shell prompts after launching
``vim`` / ``watch`` / ``tail -f`` — the kind of guidance that would
deadlock an agent following naïve "wait for the prompt between each
step" advice.

```{fastmcp-prompt-input} build_dev_workspace
```

Pass e.g. ``"tail -f /var/log/syslog"`` on Linux or
``"log stream --level info"`` on macOS as the ``log_command`` to
override the OS-neutral default.

**Sample render** (``session_name="dev"``):

````markdown
Set up a 3-pane development workspace named
'dev' with editor on top, a shell on the bottom-left, and
a logs tail on the bottom-right:

1. `create_session(session_name="dev")` — creates the
   session with a single pane (pane A, the editor). Capture the
   returned `active_pane_id` as `%A`.
2. `split_window(pane_id="%A", direction="below")` — splits off
   the bottom half (pane B, the terminal). Capture the returned
   `pane_id` as `%B`.
3. `split_window(pane_id="%B", direction="right")` — splits pane B
   horizontally (pane C, the logs pane). Capture the returned
   `pane_id` as `%C`.
4. Launch the editor and the log command via {tooliconl}`send-keys`:
   `send_keys(pane_id="%A", keys="vim")` and
   `send_keys(pane_id="%C", keys='watch -n 1 date')`. Leave pane B
   at its fresh shell prompt — nothing needs to be sent there. No
   pre-launch wait is required: tmux buffers keystrokes into the
   pane's PTY whether or not the shell has finished drawing, so
   `send_keys` immediately after `split_window` is safe and
   shell-agnostic.
5. Optionally confirm each program drew its UI via
   `wait_for_content_change(pane_id="%A", timeout=3.0)`
   (and similarly for `%C`). This is a "did the screen change?"
   check — it works whether the pane shows a prompt glyph, a vim
   splash screen, or a log tail, so no shell-specific regex is
   needed.

Use pane IDs (`%N`) for all subsequent targeting — they are stable
across layout changes; window renames are not.
````

---

```{fastmcp-prompt} interrupt_gracefully
```

**Use when** the agent needs to stop a running command and confirm
control returned to the shell — without escalating beyond SIGINT.

**Why use this instead of just sending `C-c`?** The recipe pairs the
interrupt with a {tool}`wait-for-text` against a common shell prompt
glyph and an explicit instruction to *stop and ask* if the wait
times out. That prevents the most dangerous failure mode — an agent
auto-escalating to ``C-\\`` (SIGQUIT, may core-dump) or ``kill``
without operator consent — by drawing a clear escalation boundary.

```{fastmcp-prompt-input} interrupt_gracefully
```

**Sample render** (``pane_id="%1"``):

````markdown
Interrupt whatever is running in pane %1 and
verify that control returns to the shell:

1. `send_keys(pane_id="%1", keys="C-c", literal=False,
   enter=False)` — tmux interprets `C-c` as SIGINT.
2. `wait_for_text(pane_id="%1", pattern="\$ |\# |\% ",
   regex=True, timeout=5.0)` — waits for a common shell prompt
   glyph. Adjust the pattern to match the user's shell theme.
3. If the wait times out the process is ignoring SIGINT. Stop and
   ask the caller how to proceed — do NOT escalate automatically
   to `C-\` (SIGQUIT) or `kill`.
````

The shell-prompt regex covers default bash / zsh — adjust for fish
(``> ``), zsh + oh-my-zsh (``➜ ``), or starship (``❯ ``). When the
pattern doesn't match the user's prompt theme the recipe times out
and surfaces the situation to the caller, which is the right
default for "I tried, can't tell, what should I do?" workflows.
