(waiting)=

# Waiting

An agent driving a terminal spends most of its time waiting. This page
covers which wait to reach for, why the wait tools are bounded, and — for
anyone tempted to redesign them — what was measured and rejected.

## Pick the cheapest wait that works

Reach for them in this order.

**You wrote the command: use {tooliconl}`run-command`.** It runs the
command, waits for it, and hands back output plus exit status in one call.
Measured against `sleep 2; echo done`: one call, 2.10 s, 81 tokens, correct.

**You wrote the command but want it in an interactive pane: compose a
channel.** Append `; tmux wait-for -S NAME` and block on
{tooliconl}`wait-for-channel`. This is the only *deterministic* wait —
tmux blocks server-side and returns on the signal itself, one tmux process
for the whole wait, and a 16-token result. Nothing here is inferred from
pixels.

```
send_keys("pytest; tmux wait-for -S tests_done")
wait_for_channel("tests_done")
```

The `;` fires the signal whether `pytest` passed or failed, so the wait
cannot deadlock on failure.

**You did not write the command: use {tooliconl}`wait-for-text`.** A
daemon printing `ready`, a dev server you attached to, a build someone
else started. This is the only case where scraping the pane is the right
answer, and it is a heuristic — see below.

**You want to keep watching rather than wait once: use
{tooliconl}`capture-since`.** It returns an opaque cursor you pass back,
so repeated observation does not re-send content you already have.

Always pass `stop` to {tooliconl}`wait-for-text` when a failure marker
exists. Measured on a five-minute build failing after 5 s:
`stop=["error:", "FAILED"]` returned in 4.57 s and 91 tokens; without it
the same wait ran to the ceiling — 6.6x the wall-clock and 3.9x the
tokens for a worse answer.

## Why the wait tools are bounded

Every wait is capped by `LIBTMUX_MCP_WAIT_MAX_SECONDS` (default 30 s,
hard limit 120 s). An over-large `timeout` is clamped, not rejected, and
the result reports the value actually enforced on `effective_timeout`.

**The ceiling bounds the agent's turn, not the transport.** This is worth
stating plainly because the opposite is intuitive and wrong. The wait
tools await throughout, and FastMCP serves requests concurrently over one
stdio connection, so a long wait does not block anything else. Measured
on fastmcp 3.4.4:

| tool under test | interleaved calls served | median latency |
| --- | --- | --- |
| awaits for 6 s (what the wait tools do) | 58 | 3.4 ms |
| blocks the event loop for 6 s (control) | 1 | 6014 ms |

What an unbounded wait actually costs is the agent's turn: it picks the
wrong search text once, and MCP gives it no way to change its mind
mid-call. The ceiling makes that failure cheap and repeatable instead of
terminal.

## How wait_for_text decides something is new

tmux has **no hook that fires on pane output** — the `notify_*` set in
`notify.c` covers session, window, and client lifecycle only. So any
"wait for text" must poll, tap the pty, or attach a control-mode client.
{tooliconl}`wait-for-text` polls.

At entry it records an absolute grid anchor (`history_size + cursor_y`)
and snapshots the content of the entry cursor row and everything below
it. Each tick it re-captures from the anchor and drops rows whose content
was in that snapshot. New content matches; pre-existing paint does not.

Two consequences worth knowing:

- **The entry cursor row counts.** On a quiescent pane the cursor sits at
  the end of the prompt, so the first line a command prints lands on that
  row. It is anchored on, not skipped, and single-line output and
  carriage-return spinners both match.
- **A stale match does not end the wait.** If your pattern is already on
  screen when you call, the wait keeps waiting for a *fresh* occurrence
  and reports `matched_at_entry: true` if none arrives. That is what makes
  re-running a command whose output looks identical work.

Read the result fields rather than guessing:

| field | what it tells you |
| --- | --- |
| `saw_new_output: false` | the pane really was quiet — suspect the command never ran |
| `saw_new_output: true`, `found: false` | output arrived and did not match — read `tail`, fix the pattern |
| `matched_at_entry: true` | the text is on screen but was already there when you called |
| `outcome: "alternate_screen"` | a pager or TUI owned the pane; read the screen, do not retry |
| `outcome: "stopped"` | a `stop` marker hit — `matched_index` says which |

Where it is genuinely weak: output that outruns `history-limit` between
polls. The tool detects that band and emits a warning telling you to use
{tooliconl}`wait-for-channel`, because it cannot be fixed by polling
harder.

## Designs that were measured and rejected

Recorded so they are not re-litigated from intuition.

**Background tasks (MCP SEP-1686, `task=True`).** Rejected. The
connection it would free is already free (table above). Worse, progress
notifications are silently dropped in task mode — roughly 1200
`ctx.report_progress` calls produced zero `notifications/progress` frames
against four in the synchronous control. And no client asks for it: of
the four CLIs measured, none advertised a `tasks` capability in their
`initialize` frame. The shipped bounded, cancellable design was verified
to work verbatim under task mode, so this stays a small follow-up if
clients ever arrive.

**Event-driven `pipe-pane`.** Rejected. `cmd-pipe-pane.c` keeps a single
`wp->pipe_fd` per pane and starting a new pipe frees the old one, so an
internal event pipe would silently destroy any logging a user started
with {tooliconl}`pipe-pane`, and vice versa. It also matches the raw pty
byte stream rather than the rendered grid, which fires on the echo of the
command just sent and never fires on unterminated prompts. Latency was
not the deciding factor either way: polling at the default 50 ms interval
wakes in ~21 ms median, against ~18 ms for a control-mode client.

**Removing the tool.** Rejected. The wait does not disappear with the
tool — it moves into the agent's turn loop, where it has no ceiling at
all, because MCP gives an agent no sleep primitive. Measured on a
45-second dev-server start: {tooliconl}`wait-for-text` took 2 calls and
538 wire tokens; the same goal polled via {tooliconl}`capture-since` took
10 calls and 3438.
