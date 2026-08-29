"""Reusable workflow recipes for the libtmux-mcp prompt surface.

Each function here is a FastMCP prompt — a template that returns the
text MCP clients should send to their model. Prompts are the
protocol-level way to package operator-discovered best practices.
The authoritative narrative lives at :doc:`docs/topics/prompting`; the
prompts here are the machine-facing counterpart to a few of those
recipes. Keep the set small and deliberate.
"""

from __future__ import annotations

import re

#: A tmux pane id is ``%`` followed by digits and nothing else, so a
#: value that is not one cannot name a pane.
_PANE_ID_RE = re.compile(r"^%\d+$")

#: Characters a renderer treats as structure rather than as a name.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _validated_pane_id(value: str) -> str:
    """Return *value* if it is a pane id, else refuse.

    Prompt arguments are interpolated into PROSE, not only into the
    code blocks, and the free-text arguments are already ``repr``'d
    there. The identifier arguments were not -- so a ``pane_id``
    carrying a newline put its own text at the start of a line of
    instructions, and repeated it at every mention. The split was
    backwards: the arguments that legitimately carry arbitrary content
    were escaped, and the ones with a trivial format were not.

    Validating beats escaping here because the format admits nothing to
    escape.
    """
    if not _PANE_ID_RE.match(value):
        msg = (
            f"pane_id must look like '%1' (got {value!r}). Prompt text is "
            "rendered as instructions, so a value that is not a pane id "
            "would be read as part of them."
        )
        raise ValueError(msg)
    return value


def _validated_session_name(value: str) -> str:
    """Return *value* if it can name a session, else refuse.

    Looser than :func:`_validated_pane_id`, because session names are
    nearly free-form. The line is drawn at characters that would
    restructure the rendered text rather than name anything; tmux
    itself refuses ``:`` and ``.``.
    """
    if not value or _CONTROL_RE.search(value) or {":", "."} & set(value):
        msg = (
            f"session_name may not be empty or contain control characters, "
            f"':' or '.' (got {value!r}). tmux rejects the punctuation, and "
            "prompt text is rendered as instructions, so a newline would "
            "start a line of them."
        )
        raise ValueError(msg)
    return value


def run_and_wait(
    command: str,
    pane_id: str,
    timeout: float = 60.0,
) -> str:
    """Run a shell command in a tmux pane and wait for completion.

    The returned template teaches the high-level authored-command
    primitive: ``run_command`` sends the command, waits through a
    private tmux signal, captures output, and reports exit status in
    one typed result. Use lower-level ``send_keys`` +
    ``wait_for_channel`` only when the caller needs custom shell
    composition outside this common command-completion shape.

    Parameters
    ----------
    command : str
        The shell command to run.
    pane_id : str
        Target pane (e.g. ``%1``).
    timeout : float
        Maximum seconds to wait for command completion. Default 60.
    """
    pane_id = _validated_pane_id(pane_id)
    multiline = "\n" in command or "\r" in command
    history_argument = "    suppress_history=False,\n" if multiline else ""
    history_warning = (
        "\n\nThis multiline command disables best-effort shell-history "
        "suppression and may be recorded by the shell."
        if multiline
        else ""
    )
    return f"""Run this shell command in tmux pane {pane_id}, wait until it
finishes, and inspect the typed result:

```python
result = run_command(
    pane_id={pane_id!r},
    command={command!r},
{history_argument}    timeout={timeout},
    max_lines=100,
)
```{history_warning}

Use `result.exit_status`, `result.timed_out`, and `result.output`
to decide what happened. Do NOT use a `send_keys` + `capture_pane`
retry loop for authored commands — `run_command` already performs
deterministic completion and returns tail-preserved output.

`timeout` is capped by the server's wait ceiling (30 s by default), so
read `result.effective_timeout` rather than assuming the value above was
honoured. For work that legitimately runs longer, call again — the
command keeps running in the pane between calls.

If the task needs persistent shell state or TUI keystrokes instead of
a one-shot shell command, use `send_keys` or `send_keys_batch`, then
observe later output with `capture_since`.
"""


def diagnose_failing_pane(pane_id: str) -> str:
    """Gather pane context and propose a root-cause hypothesis.

    Uses ``snapshot_pane`` (content + cursor + mode + scroll state
    in one call) instead of ``capture_pane`` + ``get_pane_info`` so
    the agent sees everything in a single protocol call. When the
    diagnosis needs another read after waiting or observing, the
    rendered recipe points agents at ``capture_since`` instead of a
    repeated full capture.

    Parameters
    ----------
    pane_id : str
        The pane to diagnose.
    """
    pane_id = _validated_pane_id(pane_id)
    return f"""Something went wrong in tmux pane {pane_id}. Diagnose it:

1. Call `snapshot_pane(pane_id="{pane_id}")` to get content,
   cursor position, pane mode, and scroll state in one call.
2. If the content looks truncated, re-call with `max_lines=None`.
3. If you need to watch the pane across more than one turn, call
   `capture_since(pane_id="{pane_id}")`, keep the returned cursor,
   and pass it to later `capture_since(cursor=...)` calls.
4. Identify the last command that ran (look at the prompt line and
   the line above it) and the last non-empty output line.
5. Propose a root cause hypothesis and a minimal command to verify
   it (do NOT execute anything yet — produce the plan first).
"""


def build_dev_workspace(
    session_name: str,
    log_command: str = "watch -n 1 date",
) -> str:
    """Construct a simple 3-pane development session.

    Produces editor (top), terminal (bottom-left), logs (bottom-right)
    layout — the most common shape for a working session.

    Parameters
    ----------
    session_name : str
        Name for the new session.
    log_command : str
        Command to run in the logs pane. Defaults to an OS-neutral
        ``watch -n 1 date`` so the recipe does not assume Linux log
        paths. Pass e.g. ``"tail -f /var/log/syslog"`` on Linux or
        ``"log stream --level info"`` on macOS.
    """
    session_name = _validated_session_name(session_name)
    return f"""Set up a 3-pane development workspace named
{session_name!r} with editor on top, a shell on the bottom-left, and
a logs tail on the bottom-right:

1. `create_session(session_name="{session_name}")` — creates the
   session with a single pane (pane A, the editor). Capture the
   returned `active_pane_id` as `%A`.
2. `split_window(pane_id="%A", direction="below")` — splits off
   the bottom half (pane B, the terminal). Capture the returned
   `pane_id` as `%B`.
3. `split_window(pane_id="%B", direction="right")` — splits pane B
   horizontally (pane C, the logs pane). Capture the returned
   `pane_id` as `%C`.
4. Launch the editor and the log command via `send_keys`:
   `send_keys(pane_id="%A", keys="vim")` and
   `send_keys(pane_id="%C", keys={log_command!r})`. Leave pane B
   at its fresh shell prompt — nothing needs to be sent there. No
   pre-launch wait is required: tmux buffers keystrokes into the
   pane's PTY whether or not the shell has finished drawing, so
   `send_keys` immediately after `split_window` is safe and
   shell-agnostic.
5. Optionally confirm each program drew its UI via
   `wait_for_text(pane_id="%A", patterns=null, timeout=3.0)`
   (and similarly for `%C`). Omitting `patterns` makes this a
   "did anything new get printed?" check — it works whether the
   pane shows a prompt glyph, a vim splash screen, or a log tail,
   so no shell-specific regex is needed.

Use pane IDs (`%N`) for all subsequent targeting — they are stable
across layout changes; window renames are not.
"""


def interrupt_gracefully(pane_id: str) -> str:
    r"""Interrupt a running command and verify the prompt returned.

    Sends ``C-c`` through ``send_keys(literal=True)``, then waits on
    shell-prompt patterns via ``wait_for_text``. Fails loudly if the
    process ignores SIGINT — the right escalation point is the caller,
    not an automatic ``C-\`` follow-up (SIGQUIT can core-dump).

    Parameters
    ----------
    pane_id : str
        Target pane.
    """
    pane_id = _validated_pane_id(pane_id)
    return f"""Interrupt whatever is running in pane {pane_id} and
verify that control returns to the shell:

1. `get_pane_info(pane_id="{pane_id}")` — note `pane_current_command`.
   That is the thing you are trying to change, and comparing it
   before and after is the only reliable answer.
2. `send_keys(pane_id="{pane_id}", keys="C-c", literal=False,
   enter=False)` — tmux interprets `C-c` as SIGINT.
3. `wait_for_text(pane_id="{pane_id}", patterns=["\\$", "\\#", "\\%", ">"],
   regex=True, timeout=5.0)` — waits for a common shell prompt glyph.
   Adjust the patterns to match the user's shell theme.
   Do NOT put `\\^C` in `stop`: the terminal echoes `^C` whenever SIGINT
   is DELIVERED, whether or not the process dies, so it marks the
   success path as a failure. Measured on sh and bash — the process
   exited and the wait still returned `outcome="stopped"`.
   The `wait_for_channel` pattern doesn't apply here — `C-c` is a
   signal, not a shell command, so there's no statement to compose
   `tmux wait-for -S` into.
4. Re-read `get_pane_info(pane_id="{pane_id}")`. If
   `pane_current_command` is back to a shell, the interrupt worked —
   trust this over the wait result, which can time out on a prompt
   whose glyph you did not predict.
5. Only if the command is UNCHANGED is the process ignoring SIGINT.
   Stop and ask the caller how to proceed — do NOT escalate
   automatically to `C-\\` (SIGQUIT) or `kill`.
"""
