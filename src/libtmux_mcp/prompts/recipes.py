"""Reusable workflow recipes for the libtmux-mcp prompt surface.

Each function here is a FastMCP prompt — a template that returns the
text MCP clients should send to their model. Prompts are the
protocol-level way to package operator-discovered best practices.
The authoritative narrative lives at :doc:`docs/topics/prompting`; the
prompts here are the machine-facing counterpart to a few of those
recipes. Keep the set small and deliberate.
"""

from __future__ import annotations

import uuid


def run_and_wait(
    command: str,
    pane_id: str,
    timeout: float = 60.0,
) -> str:
    """Run a shell command in a tmux pane and wait for completion.

    The returned template teaches the model the safe composition
    pattern — always emit ``tmux wait-for -S`` on both success and
    failure paths so a crash never deadlocks the agent on an
    edge-triggered signal. See ``docs/topics/prompting.md``.

    Each invocation embeds a fresh UUID-scoped channel name so
    concurrent agents (or parallel prompt calls from a single agent)
    cannot cross-signal each other on tmux's server-global channel
    namespace — the channel is unique to this one prompt rendering.

    Parameters
    ----------
    command : str
        The shell command to run.
    pane_id : str
        Target pane (e.g. ``%1``).
    timeout : float
        Maximum seconds to wait for the signal. Default 60.
    """
    channel = f"libtmux_mcp_wait_{uuid.uuid4().hex[:8]}"
    shell_payload = (
        f"{command}; __mcp_status=$?; tmux wait-for -S {channel}; exit $__mcp_status"
    )
    return f"""Run this shell command in tmux pane {pane_id} and block
until it finishes, preserving the command's exit status:

```
send_keys(
    pane_id={pane_id!r},
    keys={shell_payload!r},
)
wait_for_channel(channel={channel!r}, timeout={timeout})
capture_pane(pane_id={pane_id!r}, max_lines=100)
```

After the channel signals, read the last ~100 lines to verify the
command's behaviour. Do NOT use a ``capture_pane`` retry loop —
``wait_for_channel`` is strictly cheaper in agent turns.
"""


def diagnose_failing_pane(pane_id: str) -> str:
    """Gather pane context and propose a root-cause hypothesis.

    Uses ``snapshot_pane`` (content + cursor + mode + scroll state
    in one call) instead of ``capture_pane`` + ``get_pane_info`` so
    the agent sees everything in a single protocol call.

    Parameters
    ----------
    pane_id : str
        The pane to diagnose.
    """
    return f"""Something went wrong in tmux pane {pane_id}. Diagnose it:

1. Call ``snapshot_pane(pane_id="{pane_id}")`` to get content,
   cursor position, pane mode, and scroll state in one call.
2. If the content looks truncated, re-call with ``max_lines=None``.
3. Identify the last command that ran (look at the prompt line and
   the line above it) and the last non-empty output line.
4. Propose a root cause hypothesis and a minimal command to verify
   it (do NOT execute anything yet — produce the plan first).
"""


def build_dev_workspace(session_name: str) -> str:
    """Construct a simple 3-pane development session.

    Produces editor (top), terminal (bottom-left), logs (bottom-right)
    layout — the most common shape for a working session.

    Parameters
    ----------
    session_name : str
        Name for the new session.
    """
    return f"""Set up a 3-pane development workspace named
{session_name!r} with editor on top, a shell on the bottom-left, and
a logs tail on the bottom-right:

1. ``create_session(name="{session_name}")`` — creates the session
   with a single pane (pane A, the editor).
2. ``split_window(target=pane A, vertical=True)`` — splits off the
   bottom half (pane B, the terminal).
3. ``split_window(target=pane B, vertical=False)`` — splits pane B
   horizontally (pane C, the logs pane).
4. Send ``vim``, an idle shell, and ``tail -f /var/log/syslog``
   respectively using ``send_keys``. Always wait for the prompt via
   ``wait_for_text`` before moving to the next step.

Use pane IDs (``%N``) for all subsequent targeting — they are stable
across layout changes, window renames are not.
"""


def interrupt_gracefully(pane_id: str) -> str:
    r"""Interrupt a running command and verify the prompt returned.

    Sends ``C-c`` through ``send_keys(literal=True)``, then waits on
    a shell-prompt pattern via ``wait_for_text``. Fails loudly if the
    process ignores SIGINT — the right escalation point is the caller,
    not an automatic ``C-\`` follow-up (SIGQUIT can core-dump).

    Parameters
    ----------
    pane_id : str
        Target pane.
    """
    return f"""Interrupt whatever is running in pane {pane_id} and
verify that control returns to the shell:

1. ``send_keys(pane_id="{pane_id}", keys="C-c", literal=False,
   enter=False)`` — tmux interprets ``C-c`` as SIGINT.
2. ``wait_for_text(pane_id="{pane_id}", pattern="\\$ |\\# |\\% ",
   regex=True, timeout=5.0)`` — waits for a common shell prompt
   glyph. Adjust the pattern to match the user's shell theme.
3. If the wait times out the process is ignoring SIGINT. Stop and
   ask the caller how to proceed — do NOT escalate automatically
   to ``C-\\`` (SIGQUIT) or ``kill``.
"""
