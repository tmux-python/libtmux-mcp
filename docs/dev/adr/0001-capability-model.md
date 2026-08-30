(adr-capability-model)=

# ADR 0001: Capability model

This record was proposed in
[issue #127's source comment](https://github.com/tmux-python/libtmux-mcp/issues/127#issuecomment-5463431049).
The
[full target-state capability model](https://github.com/tmux-python/libtmux-mcp/issues/127#issuecomment-5463166342)
contains the inventory, manifest columns, CI invariants, and adoption phases.
This record stays above that implementation design: it states what the project
commits to and what it refuses to promise.

## Status

Proposed. Supersedes the former three-level capability model.

## Context

libtmux-mcp hands an agent a terminal. Everything downstream of that — which
tmux objects it can touch, what a client should prompt on, what a reader should
believe — depends on describing that capability accurately.

One ordered scale cannot do it. Running a shell command and deleting a tmux
window are different powers, not different amounts of one power, and a ladder
that ranks them forces every tool to be described by its position rather than
its behaviour. The practical symptom is that a rung has to lie about one axis to
speak about the other, and the honest statement a user needs — _this can execute
code as you_ — ends up implied by a tier name instead of stated in the tool's
own description.

The second problem is that the words available are not ours alone. MCP defines
[`readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.29.1/src/mcp/types.py#L1247-L1294)
with specific meanings and explicitly frames them as untrusted hints for client
presentation, not authorization. A server that repurposes them as severity
labels is not communicating with clients; it is corrupting a shared vocabulary.

## Decision

**CM-1 — The tmux socket is the namespace boundary, and it is only that.** A
server process pins one socket for its lifetime. That scopes which tmux objects
the structured tools can name, which is genuine accident isolation for the tool
surface. It scopes nothing about the filesystem, processes, network, or other
sockets, and the documentation never implies otherwise.

**CM-2 — Capability is described by independent properties, not a rank.**
Process reach (can this start a process or deliver client-controlled input to
one) and tmux effect (observe, change, delete) vary independently, and output
classes describe what a result can carry back. `rename_window` and
`run_shell_command` both change tmux state; only one can run code. A model with
two axes says that in one line, and a ladder cannot say it at all.

**CM-3 — Toolsets are unordered inventory sets, resolved once at startup.**
`inspect`, `manage`, `execute`, `teardown`. They shape what this server
advertises, for context reduction, model routing, and documentation navigation.
Because they are sets rather than a cumulative scale, `inspect,teardown` is
expressible; under the old ladder, deletion tools could not be enabled without
the typing tools. Tag-based inventory filtering is also how the surrounding
ecosystem already works — [FastMCP](https://gofastmcp.com)'s own config layer
exposes `include_tags` / `exclude_tags` over the same mechanism.

**CM-4 — Standard MCP annotations describe the whole tool call.** MCP defines
[`readOnlyHint: true`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.29.1/src/mcp/types.py#L1262-L1266)
as a claim that the tool does not modify its environment, and
[FastMCP passes annotations through](https://github.com/jlowin/fastmcp/blob/v3.4.7/fastmcp_slim/fastmcp/tools/base.py#L234-L242).
Tmux operations therefore use conservative hints when aliases or hooks make the
whole call unknowable. Toolset, process reach, and output classes describe the
direct operation without redefining the protocol.

**CM-5 — One checked-in manifest is the single source of truth, and CI asserts
against sinks rather than names.** Registration, filtering, generated docs,
badges, the README inventory, and the capabilities resource all derive from one
table. Invariants are asserted on where a parameter's value lands in the callee,
not on what the parameter is called: tmux expands formats in argument positions
whose names give no hint of it, so a field-name filter would pass a shell sink
named `start_directory`.

**CM-6 — Client-authored host commands have no direct MCP surface.**
`run_shell_command` sends authored commands to a pane, where they are attachable,
observable, and tied to a completion protocol. No public schema accepts a host
command, and this server never hands caller text directly to a host-side shell.
A pane command can still invoke tmux's
[`run-shell`](https://github.com/tmux/tmux/blob/3.7c/cmd-run-shell.c#L201-L210)
or install a
[`#()` status job](https://github.com/tmux/tmux/blob/3.7c/format.c#L416-L422),
so this is a direct-surface claim, not transitive confinement.

**CM-7 — Disclosure is a product surface, held to the same standard as
behaviour.** The install statement, the generated opening sentences, the startup
record, the trust-model page, and the audit record are all deliverables, and a
repository lint rejects new affirmative sandbox or containment language while
permitting negative boundary disclosures.

## What this does not guarantee

Each of these is stated because a reader could otherwise reasonably infer it.

**Not containment.** Execute tools run commands with the user's full authority.
A command running in a pane can reach any file, process, or network the user
can, and can open any other tmux socket by hand. OS accounts, containers, and
VMs are the isolation boundary; this server is not one. We do not build a
sandbox because a server that cannot confine its own child processes cannot
honestly claim to.

**Toolset filtering is not authorization.** Dropping `teardown` removes the
direct deletion tools from the advertised inventory. It does not stop an enabled
execute tool from typing the equivalent tmux command. We ship it as inventory
configuration and accident reduction, and refuse to describe it as a permission
system, because a bypass that is one `send_keys` away is not a boundary.

**Annotations include ambient tmux behaviour.** tmux
[expands command aliases](https://github.com/tmux/tmux/blob/3.7c/cmd-parse.y#L776-L794)
before dispatch and
[runs after-hooks](https://github.com/tmux/tmux/blob/3.7c/cmd-queue.c#L649-L663)
after it. A nominally observational call can therefore execute or mutate.
Project metadata records the intended direct operation; standard hints make no
narrower claim.

**The dedicated socket is separation, not exclusive ownership.** Objects on it
are reachable by every process of the same user that connects — a server left by
a previous run, a session made by hand, a second concurrent instance. Exclusive
ownership would need an advisory lease and an explicit adoption step for a
server of unknown provenance. Until that exists we say separation from the
user's ordinary tmux world, and never "only this agent's objects."

**`inspect` means "does not interpret client input as executable", not "safe".**
Inspect tools can return credentials, command lines, environment values, and
terminal output containing prompt-injection text. Terminal-content reads are
therefore advertised open-world. Blanket auto-approval of the whole toolset is a
client's decision to make with that stated, not something the name endorses.

**No payload inspection on typed input.** We do not scan `send_keys` or
`run_shell_command` for dangerous content. That race is unwinnable, and a filter
that catches enough examples to look protective is worse than none, because it
teaches operators to rely on it.

**Bounded matching is a mechanism we must supply, not something the language
gives us.** CPython's
[`re`](https://github.com/python/cpython/blob/v3.14.0/Lib/re/__init__.py) has no
execution timeout, so pattern-length caps alone are not a time ceiling. Search
bounds mean a specific bounded-time engine, named at implementation, or the
guarantee is not made.

**Redaction and history suppression are scoped.** Audit redaction covers the
audit record; it does not rewrite shell history, client transcripts, pane
scrollback, process arguments, or OS observation surfaces. History suppression
is best-effort hygiene, not secret transport.

**Aggregate calls do not preserve per-inner client approval.** The single read
batch that survives aggregates authority under its own name, so a client policy
keyed on an inner tool's name will not fire. That is stated in the tool's own
description rather than papered over, and it is why the mutating and destructive
batches do not survive at all.

**Names are not yet stable.** The model depends on literal tool names as client
policy hooks, which makes the alpha's renames a one-time break rather than a
free change. `MIGRATION` carries the explicit old-to-new map; the stability
promise begins after this lands, not before.

**Self-kill guards protect one process against the direct teardown tools.** They
cover the pane, window, and session containing this server, only when it lives
on the pinned socket, and only against those typed tools — not against an
equivalent command typed through an execute tool, and not at all for a server
launched outside tmux.

## Consequences

The conservative annotation policy costs prompt granularity: tmux operations
decline every positive safety claim, so clients may prompt more. Direct-operation
distinctions move into toolset, reach, and the opening sentence, which are ours
to define.

Removing per-call socket arguments means two tmux servers require two configured
MCP entries. Deriving everything from one manifest means adding a tool is adding
a row plus a test, and a tool whose claims drift from its behaviour fails CI
rather than shipping.

The changes span two repositories: the documentation extension hardcodes the old
tier vocabulary and silently renders unknown tags as `readonly`, so it has to
grow a configurable vocabulary and release before any tag rename lands here.

Because `exit-empty` defaults on
([`options-table.c`](https://github.com/tmux/tmux/blob/3.7c/options-table.c)),
an agent that removes its own sessions empties its server without a socket-wide
kill tool, which is why none is offered.

The result is a server that is useful by default, explicit that it can execute
arbitrary code, precise about the little the socket actually scopes, and
structured so that a future tool cannot quietly acquire authority the
documentation does not admit to.
