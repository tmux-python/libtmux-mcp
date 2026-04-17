(prompts-overview)=

# Prompts

MCP prompts are reusable, parameterised text templates the server
ships to its clients. A client renders a prompt by calling
``prompts/get``; the rendered text is what the model sees.
libtmux-mcp's prompts are short *workflow recipes* — the MCP-shaped
counterpart to the longer narrative recipes in {doc}`/recipes`.

Four prompts ship today:

- {ref}`run-and-wait` — execute a shell command and block until it
  finishes, preserving exit status.
- {ref}`diagnose-failing-pane` — gather pane context and produce a
  root-cause hypothesis without taking action.
- {ref}`build-dev-workspace` — set up a 3-pane editor / shell / logs
  layout shell-agnostically.
- {ref}`interrupt-gracefully` — send SIGINT and verify the shell
  prompt returns, refusing to auto-escalate.

```{tip}
Most MCP clients render prompts via a slash-command UI
(``/<server>:<prompt>``). For tools-only clients that don't expose
prompts, set ``LIBTMUX_MCP_PROMPTS_AS_TOOLS=1`` in the server
environment to surface them as ``list_prompts`` / ``get_prompt``
tools instead.
```

---

## `run_and_wait`

```{fastmcp-prompt} run_and_wait
```

**Use when** the agent needs to execute a single shell command and
must know whether it succeeded before deciding the next step.

**Why use this instead of `send_keys` + `capture_pane` polling?**
Each rendered call embeds a UUID-scoped ``tmux wait-for`` channel,
so concurrent agents (or parallel prompt calls from one agent) can
never cross-signal each other. The server side blocks until the
channel is signalled — strictly cheaper in agent turns than a
``capture_pane`` retry loop.

```{fastmcp-prompt-input} run_and_wait
```

**Sample render** (``command="pytest"``, ``pane_id="%1"``):

````text
Run this shell command in tmux pane %1 and block
until it finishes, preserving the command's exit status:

```
send_keys(
    pane_id='%1',
    keys='pytest; __mcp_status=$?; tmux wait-for -S libtmux_mcp_wait_<uuid>; exit $__mcp_status',
)
wait_for_channel(channel='libtmux_mcp_wait_<uuid>', timeout=60.0)
capture_pane(pane_id='%1', max_lines=100)
```

After the channel signals, read the last ~100 lines to verify the
command's behaviour. Do NOT use a ``capture_pane`` retry loop —
``wait_for_channel`` is strictly cheaper in agent turns.
````

The ``__mcp_status=$?`` capture and ``exit $__mcp_status`` mean the
agent observes the command's real exit code via shell-conventional
``$?`` — even though the wait-for signal fires regardless of
success or failure.

---

## `diagnose_failing_pane`

```{fastmcp-prompt} diagnose_failing_pane
```

**Use when** something visibly went wrong in a pane and the agent
needs to investigate before deciding what to fix. Produces a plan,
not an action.

**Why use this instead of just calling `capture_pane`?** The recipe
prefers {tool}`snapshot-pane`, which returns content + cursor
position + pane mode + scroll state in one call — saving a
follow-up ``get_pane_info`` round-trip. It also explicitly forbids
the agent from acting before it has a hypothesis, which prevents
"fix the symptom" anti-patterns.

```{fastmcp-prompt-input} diagnose_failing_pane
```

**Sample render** (``pane_id="%1"``):

```text
Something went wrong in tmux pane %1. Diagnose it:

1. Call ``snapshot_pane(pane_id="%1")`` to get content,
   cursor position, pane mode, and scroll state in one call.
2. If the content looks truncated, re-call with ``max_lines=None``.
3. Identify the last command that ran (look at the prompt line and
   the line above it) and the last non-empty output line.
4. Propose a root cause hypothesis and a minimal command to verify
   it (do NOT execute anything yet — produce the plan first).
```

---

## `build_dev_workspace`

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

````text
Set up a 3-pane development workspace named
'dev' with editor on top, a shell on the bottom-left, and
a logs tail on the bottom-right:

1. ``create_session(session_name="dev")`` — creates the
   session with a single pane (pane A, the editor). Capture the
   returned ``active_pane_id`` as ``%A``.
2. ``split_window(pane_id="%A", direction="below")`` — splits off
   the bottom half (pane B, the terminal). Capture the returned
   ``pane_id`` as ``%B``.
3. ``split_window(pane_id="%B", direction="right")`` — splits pane B
   horizontally (pane C, the logs pane). Capture the returned
   ``pane_id`` as ``%C``.
4. Launch the editor and the log command via ``send_keys``:
   ``send_keys(pane_id="%A", keys="vim")`` and
   ``send_keys(pane_id="%C", keys='watch -n 1 date')``. Leave pane B
   at its fresh shell prompt — nothing needs to be sent there. No
   pre-launch wait is required: tmux buffers keystrokes into the
   pane's PTY whether or not the shell has finished drawing, so
   ``send_keys`` immediately after ``split_window`` is safe and
   shell-agnostic.
5. Optionally confirm each program drew its UI via
   ``wait_for_content_change(pane_id="%A", timeout=3.0)``
   (and similarly for ``%C``). This is a "did the screen change?"
   check — it works whether the pane shows a prompt glyph, a vim
   splash screen, or a log tail, so no shell-specific regex is
   needed.

Use pane IDs (``%N``) for all subsequent targeting — they are stable
across layout changes; window renames are not.
````

---

## `interrupt_gracefully`

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

````text
Interrupt whatever is running in pane %1 and
verify that control returns to the shell:

1. ``send_keys(pane_id="%1", keys="C-c", literal=False,
   enter=False)`` — tmux interprets ``C-c`` as SIGINT.
2. ``wait_for_text(pane_id="%1", pattern="\$ |\# |\% ",
   regex=True, timeout=5.0)`` — waits for a common shell prompt
   glyph. Adjust the pattern to match the user's shell theme.
3. If the wait times out the process is ignoring SIGINT. Stop and
   ask the caller how to proceed — do NOT escalate automatically
   to ``C-\`` (SIGQUIT) or ``kill``.
````

The shell-prompt regex covers default bash / zsh — adjust for fish
(``> ``), zsh + oh-my-zsh (``➜ ``), or starship (``❯ ``). When the
pattern doesn't match the user's prompt theme the recipe times out
and surfaces the situation to the caller, which is the right
default for "I tried, can't tell, what should I do?" workflows.
