(adr-capability-model)=

# ADR 0001: Capability model

This record was proposed in
[issue #127's source comment](https://github.com/tmux-python/libtmux-mcp/issues/127#issuecomment-5463431049).
The
[full target-state capability model](https://github.com/tmux-python/libtmux-mcp/issues/127#issuecomment-5463166342)
contains the inventory, manifest columns, CI invariants, and adoption phases.
This record defines the shared contract for tmux MCP implementations. It stays
above implementation design: what every implementation commits to and refuses
to promise.

## Status

Proposed. Supersedes the former three-level capability model.

## Context

A tmux MCP hands an agent a terminal. Everything downstream of that — which tmux
objects it can touch, what a client should prompt on, what a reader should
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

**CM-1 — A tmux socket is a namespace boundary, not a security boundary.** A
server process pins one socket for its lifetime. That scopes which tmux objects
the structured tools can name, which is genuine accident isolation for the tool
surface. It scopes nothing about the filesystem, processes, network, or other
sockets, and the documentation never implies otherwise.

**CM-2 — Describe capability with independent facts, not a rank.**
Direct process reach records whether the requested operation starts a process
or delivers client-controlled input to one. Direct tmux effect records whether
that operation observes, changes, or deletes state; output classes describe what
the result can carry back. `rename_window` changes a name, while
`run_shell_command` accepts a client-authored pane command. Whole-call MCP
annotations account separately for execution added by ambient aliases or hooks.

**CM-3 — Resolve one explicit tool surface at startup.**
The unordered toolsets are `inspect`, `manage`, `execute`, and `teardown`. At
startup, expand the selected toolsets, add named inclusions, then remove named
exclusions; exclusion wins. Reject unknown names and freeze the result for the
server's lifetime. A tool outside that surface is neither advertised nor
callable. This supports context reduction, model routing, and documentation
navigation while keeping combinations such as `inspect,teardown` expressible.

**CM-4 — Use standard MCP annotations for the whole tool call.** MCP defines
[`readOnlyHint: true`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.29.1/src/mcp/types.py#L1262-L1266)
as a claim that the tool does not modify its environment. Tmux operations
therefore use conservative hints when aliases or hooks make the whole call
unknowable. Toolset, process reach, and output classes describe the direct
operation without redefining the protocol.

**CM-5 — Generate public claims from one checked-in manifest.** Registration,
filtering, generated docs, badges, the README inventory, and the capabilities
resource all derive from one table. CI asserts where a parameter's value lands
in the callee, not what the parameter is called: tmux expands formats in
argument positions whose names give no hint of it, so a field-name filter would
pass a shell sink named `start_directory`.

**CM-6 — Do not expose host-command execution as a public tool.**
`run_shell_command` sends authored commands to a pane, where they are attachable,
observable, and tied to a completion protocol. No public schema accepts a host
command, and no implementation hands caller text directly to a host-side shell.
A pane command can still invoke tmux's
[`run-shell`](https://github.com/tmux/tmux/blob/3.7c/cmd-run-shell.c#L201-L210)
or install a
[`#()` status job](https://github.com/tmux/tmux/blob/3.7c/format.c#L416-L422),
so this is a direct-surface claim, not transitive confinement.

**CM-7 — Treat disclosure as product behaviour.** The install statement,
generated opening sentences, startup record, trust-model page, and audit record
are all deliverables. Repository lint rejects new affirmative sandbox or
containment language while permitting negative boundary disclosures.

## What this does not guarantee

Each of these is stated because a reader could otherwise reasonably infer it.

**Not containment.** Execute tools run commands with the user's full authority.
A command running in a pane can reach any file, process, or network the user
can, and can open any other tmux socket by hand. OS accounts, containers, and
VMs are the isolation boundary; a tmux MCP is not one. Implementations do not
build a sandbox they cannot enforce.

**The selected tool surface limits MCP calls, not pane authority.** A surface
without teardown tools removes direct deletion calls. It does not stop an
enabled execute tool from typing the equivalent tmux command. Surface reduction
limits accidents; it does not confine what a pane can do, because a bypass is
one `send_keys` away.

**Annotations include ambient tmux behaviour.** tmux
[expands command aliases](https://github.com/tmux/tmux/blob/3.7c/cmd-parse.y#L776-L794)
before dispatch and
[runs after-hooks](https://github.com/tmux/tmux/blob/3.7c/cmd-queue.c#L649-L663)
after it. A nominally observational call can therefore execute or mutate.
Manifest metadata records the intended direct operation; standard hints make no
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

**No payload inspection on typed input.** Implementations do not scan
`send_keys` or `run_shell_command` for dangerous content. That race is
unwinnable, and a filter that catches enough examples to look protective is
worse than none, because it teaches operators to rely on it.

**Bounded matching requires a bounded mechanism.** Input-size caps alone do not
bound execution time. Each implementation names and enforces a bounded-time
matching mechanism, or makes no time-bound claim.

**Redaction and history suppression are scoped.** Audit redaction covers the
audit record; it does not rewrite shell history, client transcripts, pane
scrollback, process arguments, or OS observation surfaces. History suppression
is best-effort hygiene, not secret transport.

**Aggregate calls do not preserve per-inner client approval.** The single read
batch that survives aggregates authority under its own name, so a client policy
keyed on an inner tool's name will not fire. That is stated in the tool's own
description rather than papered over, and it is why the mutating and destructive
batches do not survive at all.

**Tool names are client policy hooks.** Implementations change accepted tool
names only through an explicit migration. Renaming a public tool is a consent
surface change, not an internal refactor.

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

Because `exit-empty` defaults on
([`options-table.c`](https://github.com/tmux/tmux/blob/3.7c/options-table.c#L375-L380)),
tmux becomes eligible to exit only after every session is removed; attached
clients can delay exit further
([`server.c`](https://github.com/tmux/tmux/blob/3.7c/server.c#L281-L292)).
Removing one MCP instance's sessions therefore does not stop a shared server
while another session remains. Socket-wide termination stays an operator action
because it may destroy sessions the caller does not own.

The result is a shared model for tmux MCPs that are useful by default, explicit
that they can execute arbitrary code, precise about the little a socket scopes,
and structured so that a future tool cannot quietly acquire authority its
documentation does not admit to.
