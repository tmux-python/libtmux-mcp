---
name: testing-mcp-with-cli-agents
description: >-
  Test an MCP server by driving real CLI agents (Claude, Codex, Cursor, Gemini,
  Grok, agy) against it, using isolated tmux sockets and send-keys instead of
  trusting unit tests alone. Use this whenever verifying MCP-server behavior
  end-to-end, checking that a local branch or checkout works across installed
  agent CLIs, comparing trunk-vs-branch MCP behavior, driving an interactive
  agent TUI to exercise approval flows or cancellation, reproducing a tmux-MCP
  bug through a live client, or wiring a checkout into the CLIs with mcp_swap.
  Reach for it even when the user only says "test the MCP", "does the branch
  work in the agents", "drive the CLI to call the tool", or "check it across
  Codex/Gemini/Cursor" without naming tmux or sockets explicitly.
---

# Testing an MCP server through real CLI agents

Unit tests prove the server's internals; they don't prove a real agent can
discover a tool, get past its approval gate, call it, and survive cancelling it
mid-flight. This skill exercises that whole path by pointing installed CLI
agents at a checkout and driving them — with the tmux tool surface (libtmux-mcp)
as the running example, though the shape generalizes to any MCP server.

## The core idea: three tmux servers, never one

The single biggest mistake is running everything on one tmux socket. Keep three
distinct servers, each on its own socket, and the whole thing becomes safe and
observable:

| Role | Socket | Who touches it | Why separate |
|---|---|---|---|
| Your real session | default | you, interactively | must never be mutated by a test |
| Harness | `tmux -L cli-harness` | the driver: `send-keys` prompts in, `capture-pane` render out | isolates the CLI TUI you're driving |
| MCP-target | `tmux -L mcp-target`, exported as `LIBTMUX_SOCKET=mcp-target` to the server | the MCP server, when the agent calls tmux tools | independent ground-truth; a destructive tool here can't kill the agent you're driving |

The server resolves its socket from `LIBTMUX_SOCKET` and runs `tmux -L <name>`
(`src/libtmux_mcp/_utils.py` builds argv with `-L server.socket_name`, defaulted
from that env var). Exporting `LIBTMUX_SOCKET=mcp-target` into the server's
config env fully sandboxes every tmux tool call onto a scratch server.
Pre-create it so the agent has something to see:

```console
$ tmux -L mcp-target new-session -d -s scratch
```

Keeping **harness** separate from **MCP-target** is the load-bearing part: if the
agent's own TUI pane lived on the socket its MCP server mutates, one
`kill-server` / `kill-session` tool call would tear down the agent mid-test, and
your captures would be polluted by the agent's own UI redraws.

## Climb only as high as the question needs — three fidelity layers

### Layer 0 — Direct MCP smoke, no CLI at all

Fastest and most deterministic. Drive the server over stdio from a tiny FastMCP
client against an isolated socket and assert the wire contract directly: the
tool list, a couple of representative calls, an error path. Use this to answer
"is the tool surface and result shape correct?" before spending a CLI on it.

Shape-normalization gotchas seen in practice — normalize before asserting:
- Match the current `LIBTMUX_SAFETY` tier: destructive-batch wrappers are hidden
  at the default tier, so don't assert they're visible.
- List-returning tools surface `structuredContent` as `{"result": [...]}`;
  `capture_pane` output can come back under `result` as a string. Don't assume a
  top-level `count`.

### Layer 1 — Headless CLI one-shot

Proves the real client can discover and call the tools, scriptably, with no
send-keys. Every CLI has a non-interactive mode. A cheap discovery proof (does
the client *see* the server?) is worth running before spending a model turn —
but the cheapest proof differs sharply per CLI: grok's `mcp doctor` does a real
handshake, codex's `mcp get` only parses config, and agy has no proof short of a
model call. `references/cli-matrix.md` has the verified per-CLI invocation,
isolation lever, and approval-bypass flag for all six. Two things that surprise
people: some `mcp list`/`list-tools` subcommands read the *ambient* config and
ignore your isolated one, and a mutating tool call needs a per-CLI
approval-bypass flag or it hangs on a no-TTY prompt. Flags drift — re-verify with
`--help`.

### Layer 2 — Interactive, driven by tmux send-keys

The high-fidelity path, and the only one that exercises approval flows, live
streaming, multi-turn, and cancellation. The loop:

```console
# 1. launch the agent TUI in a WIDE harness pane (so TUI text isn't wrapped)
$ tmux -L cli-harness new-session -d -s codex -x 220 -y 50
$ tmux -L cli-harness send-keys -t codex 'cd /repo && LIBTMUX_SOCKET=mcp-target codex' Enter

# 2. wait for the prompt to render, THEN type — never type blind
$ tmux -L cli-harness capture-pane -p -t codex | tail -5     # poll until ready
$ tmux -L cli-harness send-keys -t codex 'Use the tmux MCP to create a window named probe, then list windows' Enter

# 3. handle the approval gate — the #1 hang source (see below)
$ tmux -L cli-harness send-keys -t codex 'y' Enter           # or the mapped key

# 4. observe what the AGENT rendered (harness socket)
$ tmux -L cli-harness capture-pane -p -t codex | tail -30

# 5. assert GROUND TRUTH independently (mcp-target socket)
$ tmux -L mcp-target list-windows -t scratch                 # expect a 'probe' window
```

Step 5 is the whole point: it separates *"the agent said it worked"* from *"the
tool actually mutated tmux."* Layers 0 and 1 can be fooled by a hallucinated
success line; the target socket cannot.

## Two failure modes that waste the most time

**Approval gates hang naive harnesses.** The first tool use pops an approval
dialog. A driver that types the prompt and immediately waits for output waits
forever. Either pre-approve with the CLI's trust/approval flags (see the
matrix), or detect the approval prompt via `capture-pane` and answer its
keystroke *before* waiting for the result.

**Sleeping instead of waiting is flaky.** Poll `capture-pane` for a stable
completion marker — the prompt glyph returning, a known output line — rather than
`sleep N`. If you drive with libtmux-mcp itself, `wait_for_text` with a `stop`
list is the right primitive, but point it at the *harness* socket, never the
server under test.

**Submit as separate events.** Send the prompt text and `Enter` as two distinct
`send-keys` calls — then one Enter submits. Batching text and `Enter` into a
single `send-keys` is what leaves the prompt sitting unsent and makes it look
like you need a second Enter. Also mind PATH: a CLI launched inside a `-L`
harness pane runs in a non-login shell that lacks your mise/node/uv shims, so
`export` the needed bin dirs before launching it or you'll just get `command not
found`.

## High-value test: cancellation / teardown

Cancellation is invisible to the tool list and only reachable through Layer 2 —
and it is exactly where tmux-MCP servers leak. Reproduce it:

1. Prompt the agent to call a `wait_for_text` that will never match (long timeout).
2. While it's mid-call — the TUI shows a "working / esc to interrupt" state — send
   `Escape` to that pane. `Esc` during the working phase cancels the in-flight
   tool call **while keeping the MCP server subprocess alive**, which is the exact
   client-cancellation the reap path guards; `Esc` *after* a turn finishes just
   enters edit-previous mode. (Killing the pane instead tears down the whole
   server, which tests a different thing.)
3. On the **mcp-target** socket, assert no orphaned `tmux`/child process survives
   (`pgrep -f mcp-target`) and the server stays healthy. Verified through Codex:
   the `wait_for_text` call returned `Error: interrupted`, the server survived,
   and no child leaked.

A server that reaps its child on cancel passes; one that orphans it hangs
interpreter shutdown. This is a behavioral difference you cannot see from schemas.

## Comparing two versions (trunk vs a branch)

Two worktrees, two target sockets, same prompt:

```console
$ git worktree add ../mcp-trunk  origin/main
$ git worktree add ../mcp-branch <branch>
# point one CLI's config at ../mcp-trunk  (LIBTMUX_SOCKET=mcp-trunk)
# point another at ../mcp-branch (LIBTMUX_SOCKET=mcp-branch)
```

Diff three things: the **tool surface** (`mcp list-tools` or a Layer-0 tool dump,
diffed), the **rendered agent behavior** for the same prompt (capture-pane
transcripts), and the **ground-truth socket state** after the run.

## Wiring a checkout into the CLIs: mcp_swap

`scripts/mcp_swap.py` rewrites each CLI's config to `uv --directory <repo> run
<entry>` and preserves existing env on replacement:

```console
$ uv run scripts/mcp_swap.py detect                        # which CLIs are present
$ uv run scripts/mcp_swap.py status --server tmux           # current entries
$ uv run scripts/mcp_swap.py use-local --server tmux --dry-run
$ uv run scripts/mcp_swap.py use-local --server tmux
$ uv run scripts/mcp_swap.py revert                         # restore from timestamped backups
```

The short version: pass `--server tmux` (the real registration key on this
machine is `tmux`, not the derived `libtmux`); mcp_swap preserves env but does
not add new keys, so inject `LIBTMUX_SOCKET=mcp-target` via each CLI's native
`mcp add ... -e ...` or a post-swap edit; `mcp_swap use-local` mutates the user's
real CLI configs, so dry-run first and always `revert` at the end.

**Prefer zero-mutation isolation for a test.** mcp_swap is for a swap you *want*
to persist. To just exercise a checkout, use each CLI's throwaway config-home /
project-config lever instead — `references/cli-matrix.md` gives the verified one
per CLI (codex `CODEX_HOME` or `-c` overrides, grok `GROK_HOME`, agy
`--gemini_dir`, cursor/gemini project config, claude `--mcp-config
--strict-mcp-config`). All six were driven this way with their real config
confirmed byte-identical afterward, and no swap state touched. Note the machine
may already carry an un-reverted swap (all CLIs pointing at a local checkout), so
`revert` returns you to *that* state, not a pristine one — check the swap state
file before assuming.

## Cleanup checklist

```console
$ tmux -L cli-harness kill-server 2>/dev/null
$ tmux -L mcp-target  kill-server 2>/dev/null
$ uv run scripts/mcp_swap.py revert
```

Scratch sockets vanish with their server; configs restore from mcp_swap's
timestamped backups (LIFO if multiple swaps stacked).

## When NOT to reach for the full harness

If the question is purely "is the tool surface correct?" or "does this result
shape parse?", stay at Layer 0 — booting six CLIs to answer a wire-contract
question is wasted effort. Escalate to Layers 1 and 2 only when the client's
discovery, approval, streaming, or cancellation behavior is what's actually in
doubt.
