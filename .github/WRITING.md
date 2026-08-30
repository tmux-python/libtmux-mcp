# Writing

How this project writes prose, for humans and agents alike. It governs
`README.md`, `CHANGES`, commit messages, docstrings, source comments, the
Sphinx docs under `docs/`, and the MCP tool names, descriptions, and
parameter docs an agent reads at call time — every surface a reader or a
calling model reaches.

For environment setup, the gates, and pull request workflow, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Voice

Three surfaces, one voice. A docstring says what a caller may rely on; a
`CHANGES` entry says what changed; prose says what happens. All three are
present tense, lead with the thing being described, and stop. Why it was
built that way belongs in the commit message, which is timestamped and
attached to the diff.

The most useful editing operation is deleting the introductory sentence.

Lead with verbs and name concrete things. Put identifiers in backticks.
Prefer short declarative sentences, one operational fact each. Do not
explain Python to Python developers; do explain this project's semantics —
what a toolset does, what a tool will and will not touch, what a stale
pane object means.

Type annotations describe shape. Documentation describes meaning. A
sentence that restates a signature has said nothing.

Use MUST, SHOULD, and MAY only where the normative sense is meant. Say what
actually happens rather than that something is "supported".

| Instead of                       | Prefer                             |
| --------------------------------- | ----------------------------------- |
| "We added…"                      | "`send_keys` now accepts…"          |
| "New and improved"               | "`list_panes` now…"                 |
| "powerful", "seamless"           | state the capability                |
| "easily", "simply", "just"       | omit                                |
| "simple", "obvious", "intuitive" | omit                                |
| "robust"                         | name the failure that is handled    |
| "comprehensive"                  | name what is covered                |
| "production-ready"               | state the guarantee                 |
| "optimized", "blazingly fast"    | give the magnitude                  |
| "various fixes"                  | name the components                 |
| "under the hood"                 | omit unless observable              |
| "please note that", "note that"  | state the fact                      |
| "leverage", "utilize"            | "use"                               |
| "delve into"                     | "read", or omit                     |
| "best practices"                 | name the practice                   |
| "in order to"                    | "to"                                |

## Who you are writing for

The default reader is fluent in Python and new to this project. They can
read a signature; they cannot guess this project's semantics, or tmux's.
Serve them first.

A second, smaller reader works *on* libtmux-mcp or against its lower
layers: tool modules, Pydantic models, middleware, resources, prompt
templates. Serve them too, but mark their material opt-in — "for the rarer
cases", "advanced" — so the default reader knows they can stop.

Rules that follow:

- **Second person, present tense, active.** "You split the window", not "A
  pane is created". Address the reader who is doing the thing.
- **Concept before API surface.** Open by saying what the object or
  function *is* and what it does for the reader. The signature is the last
  detail they need, not the first.
- **Say when they can stop.** Lead with the default and the reassurance.
  Let a skimmer leave after one paragraph.
- **Grant permission, do not demand attention.** "Reach for this when…"
  tells readers they are in the right place without implying they must
  read on.
- **Progressive disclosure.** Order by how many readers need it: the
  common call, then the one argument a few will tune, then the lower-level
  primitive. Each step is for a smaller audience than the last.
- **Name the trade-off.** If a call costs something — an extra tmux
  round-trip, a stale object needing a refresh, a wider tool surface — say
  so, and say what it buys. State it; do not sell it.

## README

A README is the shortest path from "what is this?" to competent use, not
the project's autobiography.

The first sentence is a contract. It says what abstraction the reader has
been handed, concretely enough to tell this package apart from the
neighbouring one — a Model Context Protocol server for tmux, not a tmux
session manager and not a libtmux tutorial.

Get to a runnable command before anything the reader can skip.

Name the distribution (`libtmux-mcp`), the import (`libtmux_mcp`), and the
executable (`libtmux-mcp`) separately wherever they differ from each other.
That distinction prevents a Python-specific class of confusion.

State the minimum Python version and platform constraints in prose, not
only in badges. `requires-python` in `pyproject.toml` is the authority; the
README must agree with it.

Document the semantic model, not the flag list — what a tool call is for,
what it costs, and what it will not do. `--help` and the generated tool
schemas already enumerate arguments.

State defaults explicitly — defaults are API. State negative guarantees
where they exist: "does not modify your tmux config", "guards against
killing the pane it is running in". They establish boundaries faster than
any amount of description.

Headings stay conventional and stable, because people deep-link them.
Badges are few and load-bearing.

## MCP tool descriptions and parameter docs

A tool's docstring is not internal documentation. It is the only
instruction the calling model gets about what a tool does, when to reach
for it, and what it will not do — there is no separate user manual an
agent reads first. Write it as agent-facing prose, not as an implementation
note for the next Python maintainer.

**The leading paragraph is the description the model sees.** FastMCP's
docstring parser hands the text before the first `Parameters` / `Returns`
section to the client as `tool.description`. Everything you want an agent
to weigh before calling the tool — what it does, when to use it, when to
reach for a different tool instead, what it does *not* do — goes there, in
plain prose, before `Parameters`. Text placed only in `Parameters` or
`Returns` still ships (see below), but the description is what a client
shows first and what tool search ranks against.

**`Parameters` entries become the per-argument schema descriptions.**
Every keyword argument gets a `name : type` line and a sentence a caller
can act on: what a missing value defaults to, which values are mutually
exclusive, what shape an id string has (`'%1'` for a pane, `'@1'` for a
window, `'$1'` for a session). An argument without a description is an
argument the calling model has to guess about.

**Discovery is lexical, not semantic.** Tool search ranks a query against
each tool's name, description, and parameter names and descriptions — nothing
else, and nothing fuzzier. A tool an agent might reach for under a
synonym ("terminal", "shell", "scrollback", "multiplexer", "workspace")
should use that word somewhere in its leading paragraph, in a natural
sentence, not as a keyword-stuffed list. Pair that with an explicit
anti-trigger where the tool's name invites a false match — this server's
own instructions do exactly that for "window" and "session" against
editor, browser, and WM surfaces.

**Use when / avoid when, in prose, before the parameters.** State the
workflow this tool is for, then name the adjacent tool a caller more often
wants and why — cheaper, more precise, or returning a typed result instead
of raw text. `send_keys`'s docstring is the pattern to follow: it says what
raw key input is for, then routes an authored-command caller to
`run_command` and a wait-for-output caller to `wait_for_text` or
`capture_since`, by name, in the paragraph the model reads before deciding.

**Name collisions need a qualifier, verbs of art do not.** A tool named
after a tmux hierarchy noun a browser, editor, or window-manager MCP might
also claim — `list_windows`, `kill_session`, `show_option` — carries
`tmux` in its display title so a tool-catalog UI disambiguates it at a
glance. A tool named after a tmux-specific verb — `send_keys`,
`capture_pane`, `snapshot_pane` — is already unambiguous; adding `tmux` to
its title is chrome, not disambiguation. The title is not part of the
search corpus above; it is a human-readable label only.

**`anthropic/alwaysLoad` is a scarce hint, not a default.** A handful of
high-traffic `inspect` tools (list/inspect operations a session usually
starts with) carry this per-tool `meta` flag so a client can keep a small
tmux vocabulary visible without preloading every tool's schema. Reserve it
for tools nearly every session needs early; adding it broadly defeats the
point.

**Server-level instructions are a scarcer budget than any one tool's
docstring.** MCP caps server instructions at 2048 bytes, and this server
enforces that at startup. A gap in the tool set — something an agent might
expect that intentionally does not exist — is documented first in the
docstring of the nearest tool that explains the gap; only reach for a
server-level instruction segment when the gap is server-shaped (a whole
tool family is absent, not one function's behaviour).

**Tool error messages are read by the model, not printed to a human's
terminal.** Lead with a short category and a colon, then the concrete
detail: `"Object not found: …"`, `"Ambiguous target: …"`, `"Pane not
found: …"`. Where a caller has an obvious next step, attach it as a
recovery suggestion rather than folding it into the sentence — "call
`list_panes` to discover valid pane ids", not a paragraph of prose the
model has to parse for the actionable part. Reserve the loud, unrecoverable
category (a missing `tmux` binary, a genuine bug in this server) for
failures an operator — not the calling agent — has to fix; an
agent-correctable failure like a bad id or a toolset denial should not read as
loud as a crash.

## Documentation site voice

This section covers the *voice* of prose under `docs/`: how to frame a
narrative page or a tool page so a reader meets the idea before the tool
surface. It complements [MCP tool descriptions and parameter
docs](#mcp-tool-descriptions-and-parameter-docs) above, which governs a
different surface — the docstring an agent reads at call time, not the
page a person or agent reads on the documentation site. The two are
related but not interchangeable: a tool page's "Use when" prose is
hand-written Sphinx content next to a live-rendered schema; a docstring's
leading paragraph *is* the schema's description field. Keep them
consistent, not identical.

On this surface specifically, the default reader uses an MCP client to
control tmux through libtmux-mcp — configuring Claude, Codex, Cursor,
Gemini, or another agent. They know what a tmux server, session, window,
and pane are, but you cannot assume they know FastMCP, libtmux internals,
or the difference between MCP tools, resources, and prompts. The general
rules in [Who you are writing for](#who-you-are-writing-for) apply; one
more is specific to docs pages built from live schemas:

- **Frame by concept, not mechanism.** Do not headline prose by tmux
  flags, format tokens, schema keys, or private helper names. Name the
  user-facing idea; the mechanical vocabulary belongs in reference tables
  and generated signatures.

### Tool pages

A tool page under `docs/tools/` is a task page first and an API page
second. Keep the `{fastmcp-tool}` and `{fastmcp-tool-input}` directives
exact — they render the live schema — but make the surrounding prose
answer the operator's questions:

- **Use when** describes the practical workflow.
- **Avoid when** names the common wrong turn and points to the better
  tool.
- **Side effects** states the operational consequence plainly.
- **Examples** stay copyable, minimal, and realistic.

### What stays precise

Warm the framing, never the facts. Toolsets, exact tool names,
parameter names, environment variables, error strings, tmux targets,
format strings, JSON/TOML examples, and class or function
cross-references carry meaning in their exact form. Leave them exact and
explain them in the surrounding sentence.

### MyST cross-reference roles

Use the most specific target available, and link the first prose mention
of any symbol that has a useful destination on that page:

- `{class}`, `{meth}`, `{func}`, `{mod}`, `{exc}`, `{attr}` — Python
  objects.
- `{tool}` — code chip + full toolset badge (text + icon). Use in headers,
  bulleted lists, and tables where the badge gives scannable context.
- `{tooliconl}` — code chip + small colored icon (left). Use in inline
  paragraph text where the full badge is too heavy.
- `{toolref}` — code chip only, no badge. Use for dense inline sequences
  or where the toolset is already established.
- `{tooliconil}` / `{tooliconir}` — bare emoji inside a code chip. Use for
  compact lists and scan-heavy surfaces.
- `{ref}` / `{doc}` — documentation pages and section anchors.
- a Markdown or reference link — external projects and tools.

Tool slugs use the dash form matching the doc page filename
(`{tooliconl}\`snapshot-pane\``), not the Python symbol
(`snapshot_pane`). Plain backticks are correct for code syntax, env vars
(`LIBTMUX_SOCKET`), pydantic field names on returned models, parameter
names, and file paths that are not doc pages — anything without an
autodoc destination.

Do not rely on a later reference section to satisfy the first-mention
rule. If the first occurrence would be a heading, grid-card teaser, or
introductory sentence, link that occurrence or retitle the heading so the
first prose mention can carry the link. Leave command examples, code
blocks, Mermaid node labels, and literal configuration values as code;
link the surrounding prose instead. After the first linked mention on a
page, later mentions can stay plain unless distance or context makes
another link useful.

### Reference pages

Internal API pages under `docs/reference/api/` document modules with an
`{eval-rst}` block wrapping `.. automodule:: <module>` with `:members:`.
Use those pages for Python object reference targets; keep task workflow
and operator guidance in the narrative docs and tool pages.

### Before you commit a docs page

- Does the page open with what the feature *is*, or with how to call it?
- Can a reader who needs only the common case stop after the first
  paragraph?
- Are advanced, Python-only, or internals-heavy parts clearly marked
  opt-in?
- Is anything framed by a private helper, tmux flag, format token, or
  schema key that should be named by concept instead?
- Did you leave every tool name, table, error string, command example, and
  cross-reference exact?
- Did `just build-docs` stay clean — no new warning, no broken
  cross-reference?

## Documented examples that run

Examples in this repository are tests. This section is the contract for
writing one the test suite can actually see, and it states this repo's
real mechanism — read it before assuming the fleet-wide default applies
here unmodified.

**A fence tag is cosmetic. Only a `>>> ` prompt executes.** A block written
as

    ```python
    server = Server()
    ```

is prose that looks like a test. Nothing collects it, nothing runs it, and
it can be wrong for years. The same block written with a prompt is a test:

    ```python
    >>> server = Server()
    ```

Removing the prompts leaves a green test suite and a silently deleted
test. When editing a file that contains examples, count the prompts
before and after.

**The fence tag is `python`.** Not `pycon`, not bare.

**Where examples run, concretely, in this repo.** `[tool.pytest.ini_options]`
in `pyproject.toml` sets `testpaths = ["src/libtmux_mcp", "tests"]` and
`addopts` includes `--doctest-modules --doctest-docutils-modules`. Neither
`docs/` nor `README.md` is in `testpaths`, so a `>>> ` block in either one
does not execute today — it is prose only, however tempting the prompt
makes it look. Only docstring examples under `src/libtmux_mcp` run, via
`--doctest-modules`. `--doctest-docutils-modules` is the flag that would
also collect prompted blocks from `.md`/`.rst` files, and it activates the
moment either path is added to `testpaths` — no other configuration
change is needed. Until then, do not write a `>>> ` block in `docs/` or
`README.md` expecting it to be checked; it will not be.

**What a docstring example may use without importing it.** The root
`conftest.py` registers an autouse `add_doctest_fixtures` fixture that,
when `tmux` is on `PATH`, populates the doctest namespace with `Session`,
`Window`, `Pane` (the plain libtmux classes) and `server`, `session`,
`window`, `pane`, `request` (live instances backed by a real, isolated
tmux session created for the test). `Server` in that namespace is bound to
libtmux's `TestServer` fixture, not the plain `libtmux.Server` class — a
subclass that behaves identically but tears down every socket it opens
when the doctest finishes, so `Server()` in an example does not leak tmux
processes. Nothing else is implicitly available; import anything not on
this list.

**In practice, doctests here stay on pure helpers.** Every example under
`src/libtmux_mcp` today exercises a parser, a name validator, a redaction
helper, or similar — logic that needs no live tmux. That is a real
constraint, not a style preference: a tool function that calls
`_get_server` or otherwise touches a live `Session`/`Window`/`Pane` is
easier and more reliable to cover with a unit test using the `mcp_server`
/ `mcp_session` / `mcp_window` / `mcp_pane` fixtures (see
[CONTRIBUTING.md](CONTRIBUTING.md#tests)) than with a doctest, even though
the namespace fixture above makes live objects technically available.
Reach for a doctest when the function is pure enough that showing its
input and output *is* the documentation; reach for a unit test otherwise.

**`# doctest: +SKIP` is not permitted.** It is a workaround that tests
nothing. Fix the example or fix the code instead.

**Do not downgrade a doctest to a non-executed block to make it pass.** A
`.. code-block::` or an unprompted fence does not run. If an example
cannot pass, fix the example or fix the code.

**Option flags.** `ELLIPSIS` and `NORMALIZE_WHITESPACE` are enabled
globally via `doctest_optionflags`, so `...` elides variable output (a
pane id, a timestamp) and whitespace differences do not fail a comparison.
Reach for an inline `# doctest: +FLAG` only for the one block that needs
something beyond those two.

**Docstring examples** use the NumPy `Examples` section:

    Examples
    --------
    >>> _redact_digest("hello")
    'sha256:2cf24dba5fb0...'

**Room to grow.** `--doctest-docutils-modules` reads `.md` and `.rst`
whenever the path holding them is in `testpaths`. Adding `docs/` or
`README.md` to `testpaths` is the one configuration change that would turn
a prompted block there into a real test — decide deliberately before doing
it, since every existing unprompted example under `docs/` would then need
an audit for whether it should execute.

## The changelog

`CHANGES` is the changelog. Not `CHANGELOG.md`. It is included verbatim
into `docs/history.md` and rendered as the project's changelog page.

**Release entry boilerplate.** Every release header is `## libtmux-mcp
X.Y.Z (YYYY-MM-DD)`. The file opens with a `## libtmux-mcp 0.1.x
(unreleased)` placeholder block fenced by `<!-- KEEP THIS PLACEHOLDER ...
-->` and `<!-- END PLACEHOLDER ... -->` HTML comments — new release
entries land immediately below the END marker, never above it.

**Open with a multi-sentence lead paragraph.** Plain prose, no italic.
Open with the version as the sentence subject (*"libtmux-mcp X.Y.Z ships
…"*) so the lead is self-contained when excerpted. Two to four sentences
telling the reader what shipped and who cares — user-visible takeaways,
not internal mechanism. Cross-reference detail docs with `{ref}` to keep
the lead compact.

**Lead paragraphs are release-time material — off-limits to branches and
PRs.** The unreleased entry carries no lead paragraph and no version
summary: sections only (`### Breaking changes`, `### What's new`
deliverables, `### Fixes`, …). Speaking for the release — what the version
"is", "ships", or "focuses on" — is presumptuous before its scope is
final; only the person cutting the release writes that, and only when
they explicitly mean to release. Never write or edit a lead paragraph from
a feature branch, and never ask or imply that a release should happen.

**Each deliverable is a section, not a bullet.** Inside `### What's new`,
every distinct deliverable gets a `**Bold subheading**` naming it in user
vocabulary, followed by one to three prose paragraphs explaining what
shipped. Do not wrap a paragraph in `- ` — bullets are for enumerable
lists, not paragraph containers. Cross-link detail docs (`See {ref}\`foo\`
for details.`) so the prose stays focused.

**The deliverable test.** Before writing an entry, ask: "What's the
deliverable, in user vocabulary?" If you cannot answer in one sentence,
the entry is not ready. Mechanism — ordering internals, helper functions,
byte counters, where validation happens — belongs in PR descriptions and
code comments, not the changelog.

**Fixed subheadings**, in this order when present: `### Breaking changes`,
`### Dependencies`, `### What's new`, `### Fixes`, `### Documentation`,
`### Development`. Dev tooling — helper scripts, internal automation —
lives under `### Development`. For breaking changes, show the migration
path with concrete inline code (a `# Before` / `# After` fenced block).
Dependency floor bumps use the form ``Minimum `pkg>=X.Y.Z` (was
`>=X.Y.W`)``.

**PR refs `(#NN)`** sit at the end of each deliverable's prose paragraph,
not on every sentence.

**When bullets are appropriate.** Catch-all sections (`### Fixes`,
occasionally `### Documentation`) with three or more genuinely small items
use bullets — one line each, never paragraphs. If a bullet swells past two
lines, promote it to a `**Bold subheading**` with a prose body.

**Anti-patterns.**

- Fragile metrics: token ceilings, third-party version pins, percent
  benchmarks, exact byte counts. Describe the capability, not the math.
- Internal jargon: private symbols (leading-underscore identifiers),
  algorithm names exposed for the first time, backend scaffolding.
- Walls of text dressed up as bullets.
- Buried breaking changes — they get their own subheading at the top of
  the entry.
- Don't sell a fix: "no longer returns another command's reply", not
  "improves reliability".

**Always link autodoc'd APIs.** Any class, function, exception, attribute,
or tool slug with its own rendered page must be cited via the matching
role (`{class}`, `{func}`, `{exc}`, `{attr}`, `{tooliconl}`) — never with
plain backticks. Doc pages without an explicit ref label use `{doc}`
(`{doc}\`/tools/buffer/index\``). See [MyST cross-reference
roles](#myst-cross-reference-roles) for the full table.

**Summarization style.** When asked "what changed in the latest version?"
or similar, lead with the entry's lead paragraph (paraphrased if needed),
followed by each `**Bold subheading**` under `### What's new` with a
one-sentence summary. Cite `(#NN)` only if asked for source links. Do not
invent versions, dates, or numbers not present in `CHANGES`, and do not
quote line numbers or file offsets — those shift as the file evolves.

## Docstrings

The prime directive: never restate the type. The annotation is the source
of truth; the docstring carries what the annotation cannot.

This is documentation debt wearing a docstring:

```python
def get_id(pane: Pane) -> str:
    """Get the pane's identifier.

    Parameters
    ----------
    pane : Pane
        The pane.

    Returns
    -------
    str
        The identifier.
    """
```

Document instead the dimensions the type system cannot encode:

- **Mutation.** What it changes in place.
- **Ownership.** What the caller must close, release, or keep alive.
- **Ordering.** Whether results come back in a guaranteed order.
- **Timing.** What has finished by the time the call returns.
- **Failure.** Which exceptions are raised and what triggers each.
- **Idempotence.** Whether calling twice does anything the second time.
- **Concurrency.** Whether calls are coalesced, queued, or independent.
- **Units and ranges.** What a number means and what values are accepted.
- **Boundary behaviour.** What zero, empty, and the maximum do.
- **Platform.** Behaviour that differs by tmux version.
- **Security boundary.** What is executed, and what is only read.

The first sentence stands alone; tooling truncates there. PEP 257 applies:
triple double quotes, an imperative one-line summary ending in a period, a
blank line before any extended description. Do not repeat an
introspectable signature.

NumPy docstring style, enforced by ruff's `pydocstyle` convention rather
than relitigated in review:

```python
def add(param1: int, param2: int) -> int:
    """Short description of the function or class.

    Detailed description using reStructuredText format.

    Parameters
    ----------
    param1 : int
        Description of param1
    param2 : int
        Description of param2

    Returns
    -------
    int
        Description of the return value
    """
```

**Classes with fields** — `NamedTuple`, dataclasses, Pydantic models —
document every field in an `Attributes` section:

```python
@dataclasses.dataclass(frozen=True)
class CallerIdentity:
    """Identity of the tmux pane hosting this MCP server process.

    Attributes
    ----------
    socket_path : str | None
        tmux socket the caller's server listens on, or ``None`` outside
        tmux.
    """
```

Autodoc renders every field whether or not you describe it, so an
undocumented `NamedTuple` field ships to the API docs as "Alias for field
number 0" and a dataclass field ships bare. Document all of them — a class
with three fields and two documented still ships a stub for the third.

## Source comments

A comment ships only if it passes all three gates. Fail any: delete or
rewrite. Borderline: delete — borderline means the information is
reconstructible, which is what makes deletion cheap.

**Loss.** Three years from now, would losing this cost a maintainer real
time rediscovering intent, an invariant, a constraint, or a failure mode
the code and tests do not already make obvious?

**Elite.** Would SQLite, Redis, the Go standard library, or CPython write
this comment, at this length? Those projects state the constraint and
stop. They do not argue with an imagined objector.

**Upkeep.** Will it stay true without maintenance? A comment that
hand-syncs a value the code owns — a count, an offset, a line reference, a
duplicated constant — is false the first time that value moves.

### Ceiling

One or two lines. A comment reaching four is either carrying several
facts, in which case split it, or arguing, in which case cut it to the
fact.

Rationale, alternatives weighed, and the story of how the code got here
belong in the commit message: timestamped, attached to the exact diff,
and free to maintain.

A comment often holds both a constraint and the deliberation that found
it. Keep the constraint, cut the deliberation. "Runs at most once per
second" survives; "this is the right trade for now" does not.

### Keep

- Why over how: tmux-version quirks, protocol and compatibility
  constraints, performance tradeoffs still part of the contract.
- Invariants, preconditions, ordering, lifetime, and concurrency
  requirements that types and tests cannot express.
- Code that looks wrong but is not, so a later cleanup does not
  reintroduce the bug.
- A high-level sketch of an algorithm whose local operations do not
  reveal the whole.

### Delete

- Narration of the next lines; code translated into English.
- Restated names, types, defaults, or control flow.
- Values duplicated from the code and hand-synced.
- Justification, hedging, or apology for a choice.
- Speculation about future requirements.
- History version control already holds, including commented-out code.
- Ticket and issue numbers. They say nothing to a reader without tracker
  access, and they rot when the tracker moves. Unfinished work goes in the
  tracker, not the source.
- Transient observations — "currently", "for now", "the latest release" —
  that go stale with no nearby edit.

### The upkeep gate in practice

It reaches values that track our own code. It does not reach frozen
external facts.

Bad (Delete):

```python
# There are 321 tests to complete for servers.
```

Good (Keep):

```python
# tmux < 3.2 reports the pane ID only after the command completes,
# so this query must stay separate.
```

### Documentation exception

Doctests, minimal usage examples, and `Parameters`, `Returns`, and
`Attributes` entries on public API are exempt from the loss gate — they
serve the caller, not the maintainer. They are exempt from nothing else.
Ceiling: a good man page entry. Autodoc ships every field whether or not
you describe it, and a doctest that runs is also a test.

## Terminology and capitalization

Pick the domain noun and keep it. tmux's own hierarchy names — server,
session, window, pane — are not synonyms for "workspace", "tab", or
"split" in this project's prose; use the tmux word every time. If a tool
is named `capture_pane`, write "capture" everywhere rather than
alternating with "read", "grab", and "snapshot".

Tool slugs and Python symbols diverge on purpose — see
[MyST cross-reference roles](#myst-cross-reference-roles) for the
dash-versus-underscore split. Do not mix the two forms within one surface.

Stable vocabulary is what makes search, deep links, and an agent's
retrieval work at all.

Python and PyPI keep their own capitalisation. Distribution names are
written as they are published.

Do not write counts into prose — how many tools exist, how many tests
there are. They go stale silently and no reader needs them. Counts that
pin a fixture or guard an invariant are different, and belong in code and
its tests, not in prose.

## Markdown

Prose wraps at 80 columns. Table rows, badge lines, and long links are
exempt, because breaking them harms rendering. A pull request or issue
body does not wrap at all: GitHub renders a single newline as a space in a
file and as a line break in a comment, so a wrapped comment body arrives
as ragged stubs.

GitHub alert blocks — `> [!NOTE]`, `> [!WARNING]` — render as literal text
outside GitHub, so reserve them for at most one load-bearing warning per
document. Write the sentence so it carries the fact on its own, and a
renderer that drops the marker loses nothing.

Do not use a local absolute path or an email address in anything
published.

## Code blocks

Code blocks are paste-and-run units: pasting one block runs exactly one
intended action. Executed examples are exempt — the test suite runs them,
nobody pastes them.

- **One command per block.** Multiple steps may share a block only when
  explicitly chained with `&&`, `;`, or `\` continuations — the chain is
  then one logical command.
- **Explanations go in prose above the block**, never as `#` comments
  inside it.
- **Command menus are per-command blocks with prose lead-ins**, not
  tables.
- **Shell commands use the `console` tag with a `$ ` prefix.** This
  separates interactive commands from scripts and enables prompt-aware
  copy.
- **Split long commands with `\`** — one flag or flag+value pair per
  indented continuation line, positional arguments last.

Good — show the last ten commits as a graph:

```console
$ git log \
    --max-count=10 \
    --graph \
    --oneline
```

Bad:

```console
# Show the last ten commits as a graph
$ git log --max-count=10 --graph --oneline
```

## Commits

```
Scope(type[detail]): concise description

why: Explanation of necessity or impact.

what:
- Specific technical changes made
- Focused on a single topic
```

Keep the subject to 50 characters or fewer, excluding any trailing
`(#NN)` pull request reference, and wrap body lines at 72. Separate the
`why:` and `what:` blocks with a blank line.

Routine maintenance commits drop the colon and take a capitalised
description, which is what distinguishes them at a glance in `git log
--oneline`:

```
py(deps[dev]) Bump dev packages
ai(rules[AGENTS]) Judge comments by three gates
.tool-versions(uv) uv 0.12.3 -> 0.12.5
```

Everything that changes behaviour keeps the colon.

Common types:

- **feat**: New features or enhancements
- **fix**: Bug fixes
- **refactor**: Code restructuring without functional change
- **docs**: Documentation updates
- **chore**: Maintenance (dependencies, tooling, config)
- **test**: Test-related updates
- **style**: Code style and formatting
- **ci**: Workflow and pipeline changes
- **py(deps)**: Dependencies
- **py(deps[dev])**: Dev dependencies
- **ai(rules[AGENTS])**: AI rule updates

`mcp` is this repository's own scope for its Python package and server
behaviour — most feature, fix, refactor, and test commits here use it:

```
mcp(feat[pane_tools]): Add wait_for_text tool for terminal automation

why: Enable agents to wait for command output without manual polling

what:
- Add wait_for_text tool with configurable timeout and polling interval
- Use integrated retry logic to save agent tokens
- Add tests for timeout and match scenarios
```

For a multi-line message, use a heredoc so the formatting survives:

```console
$ git commit -m "$(cat <<'EOF'
Scope(feat[detail]): Concise description

why: Explanation of the change.

what:
- First change
- Second change
EOF
)"
```

### Release commits

Never create tags. Never push tags. The owner handles tagging and tag
pushes, because a tag triggers the publish workflow.

A release commit subject is plain and short: `Tag v<version>`. The
detailed why and what go in the body. Do not use the
`Scope(type[detail]):` format for a release — it buries the lede.

## Slop prevention

Treat AI slop as review-hostile noise, not as proof that text or code is
wrong. The goal is to maximise information density.

- **AI signatures.** No "Generated by", no conversational filler, no
  unexplained emoji, no tool metadata.
- **Brittle references.** No hard-coded line numbers, fragile file or test
  counts, dated "as of" claims, bare SHAs, or local absolute paths —
  unless they are strict evidentiary artefacts such as a benchmark log.
- **Diff narration.** Do not restate what moved, was renamed, or was
  removed in anything the reader holds alongside the diff: code,
  docstrings, README, CHANGES, or a pull request description. The diff
  and the commit message already carry it.
- **Branch-internal narrative.** Do not mention intermediate states,
  abandoned approaches, or "no longer" behaviour unless users of a
  published release actually experienced the old state.
- **Low-value scaffolding.** No ownerless TODOs, unused future-proofing,
  debug artefacts, or defensive wrappers around failure modes nothing can
  reach.
- **Prose inflation.** The diction table under [Voice](#voice) governs;
  replace an inflated word with a concrete description of behaviour,
  constraints, or trade-offs.
- **Coded labels.** Write rules and findings as plain imperatives. No
  `[R1]`, `Option B`, or any index a reader has to decode.

### Durable source links

Link to a pinned revision, never to trunk, when citing code from prose
that will outlive the moment it was written. A pinned permalink is not a
brittle reference; an unlinked SHA dropped into prose is. `blob/main/…`
links rot silently — the file moves, lines shift, and the anchor lands on
unrelated code while still resolving.

- Prefer a release tag (`blob/v0.1.0a20/…`). Most durable, and it tells
  the reader which released version the claim held for.
- Otherwise use a 7-char commit ref (`blob/9a29b1a/…`) reachable from
  trunk. Use when there is no tag or the claim is about unreleased code.
  Never a PR-head SHA — it can be rebased or garbage-collected.
- Reserve `blob/main/…` for living documents meant to always show the
  latest state, such as this contributing guide.
- Line anchors (`#L120-L145`) are only safe on a pinned ref.

### Preservation and context

Subjective cleanup must never remove load-bearing rationale. Adjudicate
comments with the [Source comments](#source-comments) gates above;
borderline cases are deleted, not kept.

- **Preserve the "why".** Never delete a comment documenting an
  invariant, a protocol constraint, a tmux-version quirk, or a security
  boundary.
- **Evidence is immune.** Preserve exact counts, dates, and SHAs when they
  serve as evidence in benchmark results, release notes, or stack traces.
- **Behaviour over inventory.** A useful description explains what
  changed for the system or user; it does not provide an inventory of
  files or functions the diff already shows.

### The published-release test

Long-running branches accumulate tactical decisions — renames, refactors,
attempts-then-reverts. When deciding what counts as branch-internal, use
trunk as the baseline, not intermediate states inside the current branch.
Ask:

> Did users of the most recently published release ever experience this
> old name, old behaviour, or bug?

If the answer is no, it is branch-internal narrative. Move it to the
commit message and describe only the final state in the artefact.

Keep in shipped artefacts: migration guides for symbols that actually
shipped, `### Fixes` entries for bugs that affected a published release,
and comments explaining why the current code looks this way to a reader
who never saw the previous version.
