# Documentation voice

This file covers the *voice* of prose under `docs/`: how to frame a
page so a reader meets the idea before the tool surface. It complements
the repository-root `AGENTS.md`, which already governs code blocks,
shell-command formatting, changelog conventions, MCP tool roles, and
MyST cross-references. When the two overlap, the root file wins; this
one answers the question it leaves open: how should the prose sound?

## Who you are writing for

The default reader uses an MCP client to control tmux through
libtmux-mcp. They may be configuring Claude, Codex, Cursor, Gemini, or
another agent; they know what a tmux server, session, window, and pane
are, but you cannot assume they know FastMCP, libtmux internals, the
tool registration layer, or the difference between MCP tools,
resources, and prompts.

A second, smaller reader works *on* libtmux-mcp or against its Python
surface: tool modules, Pydantic models, middleware, resources, prompt
templates, or docs extensions. Serve them too, but mark their material
opt-in ("advanced", "when extending the server") so the default reader
knows they can stop. Never make the common case pay a comprehension tax
for internals.

## Voice

- **Second person, present tense, active.** "You target a pane", not
  "A pane is targeted". Address the reader who is doing the thing.
- **Concept before tool surface.** Open by saying what the tool,
  resource, prompt, or setting *does* and when the reader needs it. The
  schema, parameters, safety tier, and raw JSON shape are supporting
  detail, not the lead.
- **Say when they can stop.** Lead with the default and the
  reassurance: most readers should use this tool, avoid this path, or
  stop at the common workflow. Let a skimmer leave after one paragraph.
- **Progressive disclosure.** Order by how many readers need it: the
  common workflow -> the one option a few will tune -> safety or
  failure modes -> lower-level Python internals. Each step is for a
  smaller audience than the last.
- **Name the trade-off.** If a path costs something - blocking a tool
  call, extra tmux round-trips, larger output, broader safety tier, or
  a stale object risk - say so, and say what it buys. State it; do not
  sell it.
- **Frame by concept, not mechanism.** Do not headline prose by tmux
  flags, format tokens, schema keys, or private helper names. Name the
  user-facing idea. The mechanical vocabulary belongs in reference
  tables, generated tool signatures, and API pages.

## Tool pages

Tool pages are task pages first and API pages second. Keep the
`fastmcp-tool` and `fastmcp-tool-input` directives exact, but make the
surrounding prose answer the operator questions:

- **Use when** describes the practical workflow.
- **Avoid when** names the common wrong turn and points to the better
  tool.
- **Side effects** states the safety consequence plainly.
- **Examples** stay copyable, minimal, and realistic.

Use the docs tool roles from the root `AGENTS.md`: `{tooliconl}` in
inline prose, `{toolref}` in dense explanatory sequences, and `{tool}`
where the full safety badge helps scanning.

## What stays precise

Warm the framing, never the facts. Safety tiers, exact tool names,
parameter names, environment variables, error strings, tmux targets,
format strings, JSON/TOML examples, and class or function
cross-references carry meaning in their exact form. Leave them exact
and explain them in the surrounding sentence.

## Cross-references

Point readers at the destination when their curiosity peaks, not in a
standalone "see also" pile. Link the first prose mention of any symbol
that has a useful destination on that page. This includes MCP tool
pages, Python objects, libtmux APIs, topic/configuration pages,
glossary concepts, and external tools or projects.

Use the most specific target available: `{class}`, `{meth}`, `{func}`,
`{mod}`, `{exc}`, or `{attr}` for API objects; `{tooliconl}` or
`{toolref}` for MCP tools; `{ref}` or `{doc}` for pages and section
anchors; and a Markdown link or reference link for external projects.
After the first linked mention on a page, later mentions can stay plain
unless the distance or context makes another link useful.

Do not rely on a later reference section to satisfy the first-mention
rule. If the first occurrence would be a heading, grid-card teaser, or
introductory sentence, link that occurrence or retitle the heading so
the first prose mention can carry the link. Leave command examples,
code blocks, Mermaid node labels, and literal configuration values as
code; link the surrounding prose instead.

## Reference pages

Internal API pages document modules with an `{eval-rst}` block wrapping
`.. automodule:: <module>` with `:members:`. Use those pages for Python
object reference targets; keep task workflow and operator guidance in
the narrative docs and tool pages.

## Before you commit

- Does the page open with what the feature *is*, or with how to call
  it?
- Can a reader who needs only the common case stop after the first
  paragraph?
- Are advanced, Python-only, or internals-heavy parts clearly marked
  opt-in?
- Is anything framed by a private helper, tmux flag, format token, or
  schema key that should be named by concept instead?
- Did you leave every tool name, table, error string, command example,
  and cross-reference exact?
- Did `just build-docs` stay clean - no new warning, no broken
  cross-reference?
