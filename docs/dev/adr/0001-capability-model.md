(adr-capability-model)=

# ADR 0001: Capability boundaries for tmux MCP implementations

## Abstract

This record defines the shared capability contract for tmux Model Context
Protocol (MCP) implementations. It specifies how implementations describe
available operations, how a tmux socket limits the structured tool namespace,
and what MCP annotations do and do not express. It does not prescribe an
implementation language, internal class design, or rollout sequence.

## Status

Proposed. Supersedes the former three-level capability model.

## Context and problem

A tmux MCP gives an agent a terminal. The implementation must describe that
authority without confusing tmux object selection, process execution, client
consent, and operating-system confinement.

One ordered safety scale cannot describe those independent concerns. Running a
shell command and deleting a tmux window are different capabilities, not
different amounts of one capability. MCP also defines
[`readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`](https://github.com/modelcontextprotocol/python-sdk/blob/v1.29.1/src/mcp/types.py#L1247-L1294)
for client presentation. Reusing those annotations as severity labels would
change their protocol meaning.

## Scope and non-goals

This record applies to MCP servers that expose structured tmux operations. It
defines their shared capability vocabulary, tool-surface behavior, annotation
semantics, execution boundary, and disclosure obligations.

This record does not define operating-system isolation, decide which shell
commands an operator permits, or make the model an enforcement boundary. It
does not track implementation progress or prescribe a programming language,
framework, storage format, or build system.

## Terminology

**tmux MCP implementation**
: An MCP server implementation that exposes structured tools for tmux.

**server process**
: One running instance of a tmux MCP implementation.

**structured tool**
: A typed MCP operation that targets tmux or a program running in a tmux pane.

**selected socket**
: The single tmux server socket chosen by a server process at startup.

**direct operation**
: The operation a structured tool requests, excluding behavior already
  configured in tmux, such as command aliases and hooks.

**workload process**
: A pane or host process whose behavior can be influenced by caller input. A
  control-plane process used only to issue a tmux request is not a workload
  process.

**whole-call MCP annotation**
: A standard MCP annotation describing the observable tool call as a whole,
  including configured tmux behavior activated by the direct operation.

**structured tool surface**
: The fixed set of tools a server process advertises and accepts.

**host command**
: Client-authored executable input handed directly to a process outside a tmux
  pane.

**minimal tmux configuration**
: Configuration supplied by the implementation that loads no user configuration,
  plugin, hook, or status job.

## Conformance

In this record, uppercase **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and
**MAY** use the meanings defined by
[BCP 14](https://datatracker.ietf.org/doc/html/rfc8174). Lowercase forms have
their ordinary English meanings.

A **conforming tmux MCP implementation** satisfies every applicable **MUST** and
**MUST NOT** requirement in this record. The Terminology, Conformance,
Architectural decisions, and Security and reliability considerations sections
are normative. Sections and paragraphs labeled informative do not define
conformance requirements.

## Architectural decisions

### CM-1: A tmux socket limits addressable tmux objects; it is not a security boundary

A server process **MUST** select exactly one tmux socket before exposing
structured tools. The server process **MUST** retain that socket for its
lifetime. A structured tool **MUST NOT** accept a per-call socket selector. A
structured tool **MUST NOT** address tmux objects outside the selected socket.

The default configuration **MUST** select a product-scoped dedicated socket. A
server process that creates the tmux server on that socket **MUST** use a minimal
tmux configuration. Selecting an inherited or user-configured socket **MUST**
require explicit operator configuration. A server process that finds an existing
server on the selected socket **MUST NOT** claim that server has minimal
configuration provenance.

The selected socket limits which tmux sessions, windows, and panes structured
tools can name. It does not restrict filesystem access, process execution,
network access, credentials, or access through commands running in a pane.

**Rationale, informative.** Socket pinning prevents accidental cross-server
object selection. It does not provide operating-system isolation and must not be
described as a sandbox.

### CM-2: Independent properties describe each tool's direct capability

Every structured tool **MUST** declare its direct process reach, direct tmux
effect, and output classes. An implementation **MUST NOT** collapse those
properties into an ordered safety level.

Direct process reach **MUST** use one of these values:

- `none`: starts no workload process and delivers no client-controlled input to
  one.
- `configured-process`: starts a pane workload process without accepting a
  command payload or client-controlled tmux-format input.
- `pane-input`: delivers client-controlled keys or text to a pane program.
- `pane-command`: runs a client-authored shell command in a pane.

A conforming implementation **MUST** reserve `host-command` to describe a
prohibited public capability.

Direct tmux effect **MUST** be a set containing one or more of `observe`,
`change`, and `delete`. Output classes **MUST** be a set drawn from
`tmux-metadata`, `terminal-content`, `process-environment`, and
`configured-command`. An implementation **MUST** separately record whether a
tool may expose secrets or return untrusted content.

These properties describe the direct operation. Whole-call MCP annotations
separately account for execution or mutation added by configured aliases and
hooks.

**Example, informative.** `rename_window` changes tmux state without accepting
executable input. `run_shell_command` also changes tmux state and accepts a
client-authored pane command. Their tmux effects overlap; their process reach
does not.

### CM-3: One startup decision defines the advertised and callable tool surface

Every structured tool **MUST** belong to exactly one unordered toolset:
`inspect`, `manage`, `execute`, or `teardown`.

On the default dedicated socket with minimal tmux configuration, an
implementation **MUST** enable all four toolsets by default. On an inherited or
user-configured socket, an implementation **MUST** require explicit operator
selection before enabling `teardown`. It **MUST** apply the same requirement to
an existing server whose configuration provenance is unknown.

At startup, an implementation **MUST** expand the selected toolsets. It **MUST**
then add named inclusions. It **MUST** then remove named exclusions. A named
exclusion **MUST** win over every inclusion path.

An implementation **MUST** reject unknown toolset and tool names at startup. It
**MUST** freeze the effective structured tool surface for the server process's
lifetime. A tool outside that surface **MUST NOT** be advertised. A tool outside
that surface **MUST NOT** be callable by name.

An aggregate tool **MAY** invoke a nested operation that is not separately
advertised. The aggregate tool **MUST** declare that nested operation as part of
its own authority. A named exclusion **MUST** remove an operation from every
aggregate tool's nested authority.

The toolsets support inventory configuration, context reduction, model routing,
and documentation navigation. They do not restrict what an enabled pane-input
or pane-command tool can cause a pane program to do.

**Rationale, informative.** Unordered toolsets permit combinations such as
`inspect,teardown`. Applying the same effective surface to discovery and
invocation prevents a hidden tool from remaining callable.

### CM-4: MCP annotations describe an entire tool call

An implementation **MUST** use standard MCP annotations only with their protocol
meanings. A whole-call MCP annotation **MUST** account for the direct operation
and configured tmux behavior that the operation activates. An implementation
**MUST NOT** use standard annotations as authorization decisions or project
severity labels.

Direct process reach, direct tmux effect, and output classes **MUST** remain
separate from standard MCP annotations. Without evidence for the whole-call
claim, an implementation **MUST** set `readOnlyHint` to `false`. Without evidence
for the whole-call claim, an implementation **MUST** set `destructiveHint` to
`true`. Without evidence for the whole-call claim, an implementation **MUST** set
`idempotentHint` to `false`. Without evidence for the whole-call claim, an
implementation **MUST** set `openWorldHint` to `true`.

An implementation **MUST** set `readOnlyHint` to `true` only when the whole call
cannot modify its environment. It **MUST** set `destructiveHint` to `false` only
when the whole call performs additive updates at most. It **MUST** set
`idempotentHint` to `true` only when repeating the call with the same arguments
has no additional effect. It **MUST** set `openWorldHint` to `false` only when the
whole call cannot interact with external entities.

MCP annotations support client consent interfaces. They do not enforce tool
authorization, operating-system confinement, or command policy.

### CM-5: One authoritative capability definition governs every public claim

An implementation **MUST** maintain one authoritative, machine-readable
capability definition for its structured tools. Tool registration, the effective
structured tool surface, tool descriptions, documentation, and capability
reporting **MUST** agree with that definition.

The capability definition **MUST** classify every caller-controlled input by the
interpreter boundary it reaches. An implementation **MUST** validate capability
claims against those input sinks rather than infer behavior from parameter
names.

**Rationale, informative.** tmux expands formats in argument positions whose
names do not reveal the interpreter boundary. A shared manifest generated or
validated during CI is one implementation approach, not a required storage
format.

### CM-6: Public tools do not execute client-authored host commands

A conforming implementation **MUST NOT** expose a structured tool with
`host-command` process reach. It **MUST NOT** hand caller text directly to a
host-side shell. Client-authored shell commands **MAY** run through a
`pane-command` tool, where the process is represented by a tmux pane.

This rule constrains the direct MCP surface. It does not prevent a pane command
from invoking tmux's
[`run-shell`](https://github.com/tmux/tmux/blob/3.2a/cmd-run-shell.c#L177-L181),
installing a
[`#()` status job](https://github.com/tmux/tmux/blob/3.2a/format.c#L392-L399),
or starting any process available to the tmux user's account.

### CM-7: Capability disclosure is part of the product contract

Every structured tool description **MUST** begin with a plain-language statement
of its direct process reach and tmux effect. An implementation **MUST** publish
its effective structured tool surface and selected socket. Installation and
trust documentation **MUST** state that execute tools run with the tmux user's
authority.

Documentation **MUST** distinguish socket-scoped object selection from
operating-system confinement. Documentation **MUST** distinguish tool-surface
filtering from authorization. Documentation **MUST** describe whole-call MCP
annotations as consent metadata rather than enforcement.

An implementation **MUST NOT** describe a dedicated socket, restricted tool
surface, payload filter, or MCP annotation as a sandbox or security boundary.

## Consequences

Conservative whole-call annotations may cause clients to prompt more often.
That cost preserves the protocol meaning of the annotations.

Pinning one socket per server process requires separate configured MCP entries
to control separate tmux sockets. The selected socket reduces accidental object
selection without claiming exclusive ownership.

Maintaining one authoritative capability definition adds review and validation
work. It also makes capability drift detectable across registration,
documentation, and runtime disclosure.

Stable tool names become part of the client-consent surface. Renaming a public
tool therefore requires an explicit migration rather than an internal refactor.

## Rejected alternatives

**An ordered safety scale.** One rank cannot represent independent process
reach, tmux effect, and output sensitivity without hiding one of them.

**Tool filtering as authorization.** An enabled pane-input or pane-command tool
can express operations omitted from the structured tool surface. Filtering is
still useful for inventory control and accident reduction.

**Payload blocklists.** Shell and terminal input are composable. A filter that
recognizes selected strings cannot establish a command boundary and would invite
operators to rely on incomplete protection.

**Per-call socket selection.** A caller-selected socket expands every tool's
object namespace and makes one server process represent several trust contexts.

**Public host-command tools.** Host-side commands would bypass the observable
pane process and its completion boundary.

**Generic mutating or destructive aggregates.** A wrapper hides the names on
which client consent policies depend and turns one approval into authority over
unrelated operations.

## Security and reliability considerations

### Ambient tmux behavior

**Background, informative.** tmux
[expands command aliases](https://github.com/tmux/tmux/blob/3.2a/cmd-parse.y#L698-L715)
before dispatch and
[runs after-hooks](https://github.com/tmux/tmux/blob/3.2a/cmd-queue.c#L617-L627)
after many commands. A nominally observational direct operation can therefore
execute or mutate through existing configuration. Independent pane processes,
plugins, event hooks, and status jobs can also run without an MCP call.

A conforming implementation **MUST NOT** claim that it prevents all subprocess
execution. A conforming implementation **MUST** describe ambient tmux behavior
separately from direct process reach.

### Shared sockets

**Boundary, informative.** Every process with access to the selected socket can
create or alter objects on it. A dedicated socket separates the structured
namespace from another tmux server; it does not establish exclusive ownership.

An implementation **MUST NOT** claim ownership of every object on a shared
socket. A self-kill guard **MUST** be described as protection against direct
teardown tools only, not against equivalent pane commands or other clients.

**Shutdown behavior, informative.** With the default `exit-empty` enabled and
`exit-unattached` disabled
([tmux option defaults](https://github.com/tmux/tmux/blob/3.2a/options-table.c#L256-L268)),
tmux waits for every session to be removed and for clients to disconnect before
exiting
([tmux exit logic](https://github.com/tmux/tmux/blob/3.2a/server.c#L268-L286)).
Removing one MCP instance's sessions does not stop a shared server while another
session remains. Socket-wide termination remains an operator action because it
may destroy sessions the caller does not own.

### Untrusted and sensitive output

**Background, informative.** Terminal output, environment values, configured
commands, and tmux metadata can contain secrets or untrusted instructions.

An `inspect` tool **MUST NOT** be described as safe merely because its direct
operation is observational. An implementation **MUST** disclose when a tool may
return untrusted content. It **MUST** disclose when a tool may expose secrets.

### Aggregate authority

An aggregate tool **MUST** disclose the complete set of nested tools it can
invoke. Its effective authority **MUST NOT** exceed its advertised nested tool
set. A conforming implementation **MUST NOT** expose a generic mutating or
destructive aggregate.

### Bounded operations

An implementation that claims bounded pattern matching **MUST** bound both input
size and matching execution time. A capture or transport deadline **MUST NOT** be
misreported as a matching timeout.

### Redaction and history suppression

**Boundary, informative.** Audit redaction applies only to the audit record.
History suppression is best-effort shell hygiene.

An implementation **MUST NOT** claim that either mechanism removes data from
client transcripts, pane scrollback, process arguments, shell history outside
its control, or operating-system observation surfaces.

## When to reconsider

Reconsider this decision if MCP gains enforceable authorization or nested-call
annotation semantics, if tmux exposes a public command mode that suppresses all
relevant ambient behavior, or if deployments require a stronger multi-user
boundary than one operating-system account and socket can provide.

Reconsider the shared vocabulary if an implementation cannot express a required
capability without weakening an existing term. Add a new independent property
instead of stretching an old one into a severity rank.

## References

- [Issue #127: tool visibility is not operating-system confinement](https://github.com/tmux-python/libtmux-mcp/issues/127)
- [Source decision for ADR 0001](https://github.com/tmux-python/libtmux-mcp/issues/127#issuecomment-5463431049)
- [Detailed target-state capability model](https://github.com/tmux-python/libtmux-mcp/issues/127#issuecomment-5463166342)
