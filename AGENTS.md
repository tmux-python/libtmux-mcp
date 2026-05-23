# AGENTS.md

This file provides guidance to AI agents (including Claude Code, Cursor, and other LLM-powered tools) when working with code in this repository.

## CRITICAL REQUIREMENTS

### Test Success
- ALL tests MUST pass for code to be considered complete and working
- Never describe code as "working as expected" if there are ANY failing tests
- Even if specific feature tests pass, failing tests elsewhere indicate broken functionality
- Changes that break existing tests must be fixed before considering implementation complete
- A successful implementation must pass linting, type checking, AND all existing tests

## Project Overview

libtmux-mcp is an MCP (Model Context Protocol) server for tmux, powered by [libtmux](https://github.com/tmux-python/libtmux). It gives AI agents (Claude Code, Claude Desktop, Codex CLI, Gemini CLI, Cursor) programmatic control over tmux sessions.

Key features:
- MCP tools across 6 modules: server, session, window, pane, options, environment
- `tmux://` URI resources for browsing tmux hierarchy
- Safety tier middleware (readonly, mutating, destructive)
- Socket isolation for multi-server safety
- Pydantic models for all tool outputs
- Full type safety (mypy strict)

The core tmux ORM is provided by [libtmux](https://libtmux.git-pull.com/) - this package wraps it as an MCP server.

## Development Environment

This project uses:
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) for dependency management
- [ruff](https://github.com/astral-sh/ruff) for linting and formatting
- [mypy](https://github.com/python/mypy) for type checking
- [pytest](https://docs.pytest.org/) for testing
  - [pytest-watcher](https://github.com/olzhasar/pytest-watcher) for continuous testing

## Common Commands

### Setting Up Environment

```bash
# Install dependencies
uv pip install --editable .
uv pip sync

# Install with development dependencies
uv pip install --editable . -G dev
```

### Running Tests

```bash
# Run all tests
just test
# or directly with pytest
uv run pytest

# Run a single test file
uv run pytest tests/test_pane_tools.py

# Run a specific test
uv run pytest tests/test_pane_tools.py::test_send_keys

# Run tests with test watcher
just start
# or
uv run ptw .

# Run tests with doctests
uv run ptw . --now --doctest-modules
```

### Linting and Type Checking

```bash
# Run ruff for linting
just ruff
# or directly
uv run ruff check .

# Format code with ruff
just ruff-format
# or directly
uv run ruff format .

# Run ruff linting with auto-fixes
uv run ruff check . --fix --show-fixes

# Run mypy for type checking
just mypy
# or directly
uv run mypy src tests

# Watch mode for linting (using entr)
just watch-ruff
just watch-mypy
```

### Development Workflow

Follow this workflow for code changes:

1. **Format First**: `uv run ruff format .`
2. **Run Tests**: `uv run pytest`
3. **Run Linting**: `uv run ruff check . --fix --show-fixes`
4. **Check Types**: `uv run mypy`
5. **Verify Tests Again**: `uv run pytest`

### Documentation

```bash
# Build documentation
just build-docs

# Start documentation server with auto-reload
just start-docs

# Update documentation CSS/JS
just design-docs
```

## Code Architecture

libtmux-mcp wraps libtmux's tmux hierarchy as MCP tools and resources:

```
tmux hierarchy: Server > Session > Window > Pane
```

### Core Modules

1. **Entry Point** (`src/libtmux_mcp/__init__.py`)
   - `main()` function, console script entry point
   - Guards against missing fastmcp dependency

2. **Server** (`src/libtmux_mcp/server.py`)
   - Creates and configures the FastMCP instance
   - Builds server instructions with agent context
   - Safety tier validation from `LIBTMUX_SAFETY` env var

3. **Utils** (`src/libtmux_mcp/_utils.py`)
   - Thread-safe server caching by (socket_name, socket_path, tmux_bin) tuple
   - Object resolvers: `_resolve_session()`, `_resolve_window()`, `_resolve_pane()`
   - Serializers: `_serialize_session()`, `_serialize_window()`, `_serialize_pane()`
   - QueryList filter application with validation
   - `handle_tool_errors` decorator for standardized error handling
   - Safety tier tags and annotation presets

4. **Models** (`src/libtmux_mcp/models.py`)
   - Pydantic models for all tool outputs
   - `SessionInfo`, `WindowInfo`, `PaneInfo`, `PaneContentMatch`
   - `ServerInfo`, `OptionResult`, `EnvironmentResult`, `WaitForTextResult`

5. **Middleware** (`src/libtmux_mcp/middleware.py`)
   - `SafetyMiddleware` gates tools by tier (readonly/mutating/destructive)
   - Fail-closed: tools without a recognized tier tag are denied

6. **Tools** (`src/libtmux_mcp/tools/`)
   - `server_tools.py` - list_sessions, create_session, kill_server, get_server_info
   - `session_tools.py` - list_windows, create_window, rename_session, kill_session
   - `window_tools.py` - list_panes, split_window, rename_window, kill_window, select_layout, resize_window
   - `pane_tools.py` - send_keys, capture_pane, resize_pane, kill_pane, set_pane_title, get_pane_info, clear_pane, search_panes, wait_for_text
   - `option_tools.py` - show_option, set_option
   - `env_tools.py` - show_environment, set_environment

7. **Resources** (`src/libtmux_mcp/resources/`)
   - `hierarchy.py` - 6 `tmux://` URI resources for browsing tmux hierarchy

### Safety Tiers

Tools are tagged with safety tiers:
- `readonly` - Read-only operations (list, capture, search, info)
- `mutating` - Read + write operations (create, send_keys, rename, resize)
- `destructive` - All operations including kill commands

The `LIBTMUX_SAFETY` env var controls the maximum tier. Default is `mutating`.

### libtmux Integration

This package depends on libtmux for all tmux interactions. The core types are:
- `libtmux.Server` - tmux server instance
- `libtmux.Session` - tmux session
- `libtmux.Window` - tmux window
- `libtmux.Pane` - tmux pane

See [libtmux docs](https://libtmux.git-pull.com/) for the full API.

## Testing Strategy

Tests use libtmux's pytest plugin fixtures (`server`, `session`, `window`, `pane`) which create isolated tmux sessions for each test. MCP-specific fixtures in `tests/conftest.py` register the test server in the MCP cache.

### Testing Guidelines

1. **Use functional tests only**: Write tests as standalone functions, not classes. Avoid `class TestFoo:` groupings - use descriptive function names and file organization instead.

2. **Use existing fixtures over mocks**
   - Use fixtures from conftest.py instead of `monkeypatch` and `MagicMock` when available
   - For libtmux, use provided fixtures: `server`, `session`, `window`, and `pane`
   - MCP fixtures: `mcp_server`, `mcp_session`, `mcp_window`, `mcp_pane`
   - Document in test docstrings why standard fixtures weren't used for exceptional cases

3. **Preferred pytest patterns**
   - Use `tmp_path` (pathlib.Path) fixture over Python's `tempfile`
   - Use `monkeypatch` fixture over `unittest.mock`

4. **Running tests continuously**
   - Use pytest-watcher during development: `uv run ptw .`
   - For doctests: `uv run ptw . --now --doctest-modules`

### Example Fixture Usage

```python
def test_list_sessions(mcp_server, mcp_session):
    """list_sessions returns session info."""
    result = list_sessions(socket_name=mcp_server.socket_name)
    assert len(result) >= 1
```

## Coding Standards

Key highlights:

### Imports

- **Use namespace imports for standard library modules**: `import enum` instead of `from enum import Enum`
  - **Exception**: `dataclasses` module may use `from dataclasses import dataclass, field` for cleaner decorator syntax
  - This rule applies to Python standard library only; third-party packages may use `from X import Y`
- **For typing**, use `import typing as t` and access via namespace: `t.NamedTuple`, etc.
- **Use `from __future__ import annotations`** at the top of all Python files

### Docstrings

Follow NumPy docstring style for all functions and methods:

```python
"""Short description of the function or class.

Detailed description using reStructuredText format.

Parameters
----------
param1 : type
    Description of param1
param2 : type
    Description of param2

Returns
-------
type
    Description of return value
"""
```

### Doctests

This repo is an **MCP server**, not a general-purpose library. Most tools
require a live tmux server to do anything meaningful, so a blanket
doctest mandate doesn't fit the shape of the code. Scope doctests to
functions where they actually work offline.

**Where doctests SHOULD be used:**
- Pure helper functions (parsers, formatters, digest / redaction
  logic, small utilities) that can run with no external state.
- Examples in module-level docstrings that illustrate a concept without
  hitting tmux, the filesystem, or the network.

**Where doctests are exempt:**
- Any tool function that calls `_get_server`, touches a `Session`,
  `Window`, or `Pane`, or otherwise requires tmux to be running. Use a
  unit test with fixtures instead.
- Functions that do I/O, spawn subprocesses, or read environment.

**CRITICAL RULES for doctests that exist:**
- They MUST actually execute — never comment out function calls or
  similar.
- They MUST NOT be converted to `.. code-block::` as a workaround
  (code-blocks don't run).
- `# doctest: +SKIP` is discouraged. If a function can't run offline,
  write a unit test instead of a skipped doctest — a skipped test is
  just noise.

**When output varies, use ellipsis:**
```python
>>> window.window_id  # doctest: +ELLIPSIS
'@...'
```

### Logging Standards

These rules guide future logging changes; existing code may not yet conform.

#### Logger setup

- Use `logging.getLogger(__name__)` in every module
- Add `NullHandler` in library `__init__.py` files
- Never configure handlers, levels, or formatters in library code - that's the application's job

#### Structured context via `extra`

Pass structured data on every log call where useful for filtering, searching, or test assertions.

#### Lazy formatting

`logger.debug("msg %s", val)` not f-strings. Two rationales:
- Deferred string interpolation: skipped entirely when level is filtered
- Aggregator message template grouping

### Git Commit Standards

Format commit messages as:
```
Scope(type[detail]): concise description

why: Explanation of necessity or impact.
what:
- Specific technical changes made
- Focused on a single topic
```

Common commit types:
- **feat**: New features or enhancements
- **fix**: Bug fixes
- **refactor**: Code restructuring without functional change
- **docs**: Documentation updates
- **chore**: Maintenance (dependencies, tooling, config)
- **test**: Test-related updates
- **style**: Code style and formatting
- **py(deps)**: Dependencies
- **py(deps[dev])**: Dev Dependencies
- **ai(rules[AGENTS])**: AI rule updates

Example:
```
mcp(feat[pane_tools]): Add wait_for_text tool for terminal automation

why: Enable agents to wait for command output without manual polling
what:
- Add wait_for_text tool with configurable timeout and polling interval
- Use integrated retry logic to save agent tokens
- Add tests for timeout and match scenarios
```

#### Release commits

Never create tags. Never push tags. The user handles tagging and tag
pushes (tags trigger the CI publish workflow).

Release commit subjects are plain and short: `Tag v<version>`. Put
the detailed why/what in the commit body. Don't use the
`Scope(type[detail]):` format for releases — don't bury the lede.

For multi-line commits, use heredoc to preserve formatting:
```bash
git commit -m "$(cat <<'EOF'
feat(Component[method]) add feature description

why: Explanation of the change.
what:
- First change
- Second change
EOF
)"
```

## Documentation Standards

### Sphinx Cross-Reference Roles for MCP Tools

- `{tool}` — code chip + full safety badge (text + icon). Use in **headers, bulleted lists, and tables** where the badge provides scannable context.
- `{tooliconl}` — code chip + small colored square icon (left). Use in **inline paragraph text** where the full badge is too visually heavy.
- `{toolref}` — code chip only, no badge. Use for **dense inline sequences** or explanatory text where the safety tier is already established.
- `{tooliconil}` / `{tooliconir}` — bare emoji inside code chip. Use for **compact lists and scan-heavy surfaces**.

### Code Blocks in Documentation

When writing documentation (README, CHANGES, docs/), follow these rules for code blocks:

**One command per code block.** This makes commands individually copyable. For sequential commands, either use separate code blocks or chain them with `&&` or `;` and `\` continuations (keeping it one logical command).

**Put explanations outside the code block**, not as comments inside.

### Shell Command Formatting

**Use `console` language tag with `$ ` prefix.**

Good:

```console
$ uv run pytest
```

Bad:

```bash
uv run pytest
```

**Split long commands with `\` for readability.** Each flag or flag+value pair gets its own continuation line, indented. Positional parameters go on the final line.

Good:

```console
$ claude mcp add \
    --scope user \
    tmux -- \
    uv --directory ~/work/python/libtmux-mcp \
    run libtmux-mcp
```

Bad:

```console
$ claude mcp add --scope user tmux -- uv --directory ~/work/python/libtmux-mcp run libtmux-mcp
```

### Changelog Conventions

These rules apply when authoring entries in `CHANGES`, which is included into `docs/history.md` and rendered as the Sphinx changelog page. Modeled on Django's release-notes shape — deliverables get titles and prose, not bullets.

**Release entry boilerplate.** Every release header is `## libtmux-mcp X.Y.Z (YYYY-MM-DD)`. The file opens with a `## libtmux-mcp 0.1.x (unreleased)` placeholder block fenced by `<!-- KEEP THIS PLACEHOLDER ... -->` and `<!-- END PLACEHOLDER ... -->` HTML comments — new release entries land immediately below the END marker, never above it.

**Open with a multi-sentence lead paragraph.** Plain prose, no italic. Open with the version as sentence subject (*"libtmux-mcp X.Y.Z ships …"*) so the lead is self-contained when excerpted. Two to four sentences telling the reader what shipped and who cares — user-visible takeaways, not internal mechanism. Cross-reference detail docs with `{ref}` to keep the lead compact.

**Each deliverable is a section, not a bullet.** Inside `### What's new`, every distinct deliverable gets a `**Bold subheading**` naming it in user vocabulary, followed by 1-3 prose paragraphs explaining what shipped. Don't wrap a paragraph in `- ` — bullets are for enumerable lists, not paragraph containers. Cross-link detail docs (`See {ref}\`foo\` for details.`) so prose stays focused.

**The deliverable test.** Before writing an entry, ask: "What's the deliverable, in user vocabulary?" If you can't answer in one sentence, the entry isn't ready. Mechanism (LIFO ordering, helper internals, byte counters, schema-validation locations) belongs in PR descriptions and code comments, not the changelog.

**Fixed subheadings**, in this order when present: `### Breaking changes`, `### Dependencies`, `### What's new`, `### Fixes`, `### Documentation`, `### Development`. Dev tooling (helper scripts, internal automation) lives under `### Development`. For breaking changes, show the migration path with concrete inline code (e.g. a `# Before` / `# After` fenced code block). Dependency floor bumps use the form ``Minimum `pkg>=X.Y.Z` (was `>=X.Y.W`)``.

**PR refs `(#NN)`** sit at the end of each deliverable's prose paragraph, not on every sentence.

**When bullets are appropriate.** Catch-all sections (`### Fixes`, occasionally `### Documentation`) with 3+ genuinely small items use bullets — one line each, never paragraphs. If a bullet swells past two lines, it's not a bullet anymore; promote it to a `**Bold subheading**` with prose body.

**Anti-patterns.**

- Fragile metrics: token ceilings, third-party client version pins, percent benchmarks, exact byte counts. Describe the *capability*, not the math.
- Internal jargon: private symbols (leading-underscore identifiers), algorithm names exposed for the first time, backend scaffolding.
- Walls of text dressed up as bullets.
- Buried breaking changes — they get their own subheading at the top of the entry.

**Always link autodoc'd APIs.** Any class, function, exception, attribute, or tool slug that has its own rendered page must be cited via the appropriate role (`{class}`, `{func}`, `{exc}`, `{attr}`, `{tooliconl}`) — never with plain backticks. Tool slugs use the dash form matching the doc page filename (`{tooliconl}\`snapshot-pane\``, not the Python symbol `snapshot_pane`). Doc pages without explicit ref labels use `{doc}` (e.g. `{doc}\`/tools/buffer/index\``). Plain backticks are correct for code syntax (`{user,project}`, `True`), env vars (`LIBTMUX_SOCKET`), pydantic field names on returned models (`pane_at_*`), parameter names, and file paths that aren't doc pages — anything without an autodoc destination.

**MyST roles.** Tool references use `{tooliconl}` (inline-friendly badge), class references use `{class}`, exceptions use `{exc}`, functions use `{func}`, attributes use `{attr}`, internal anchors use `{ref}`, doc-path links use `{doc}`. See **Sphinx Cross-Reference Roles for MCP Tools** above for the full table.

**Summarization style.** When a user asks "what changed in the latest version?" or similar, lead with the entry's lead paragraph (paraphrased if needed), followed by each `**Bold subheading**` under `### What's new` with a one-sentence summary. Cite `(#NN)` only if the user asks for source links. Don't invent versions, dates, or numbers not present in `CHANGES`. Don't quote line numbers or file offsets — those shift as the file evolves.

## Debugging Tips

When stuck in debugging loops:

1. **Pause and acknowledge the loop**
2. **Minimize to MVP**: Remove all debugging cruft and experimental code
3. **Document the issue** comprehensively for a fresh approach
4. **Format for portability** (using quadruple backticks)

## tmux-Specific Considerations

### tmux Command Execution

- All tmux commands go through the `cmd()` method on Server/Session/Window/Pane objects (via libtmux)
- Commands return a `CommandResult` object with `stdout` and `stderr`
- Use tmux format strings to query object state

### Object Refresh

- Objects can become stale if tmux state changes externally
- Use refresh methods (e.g., `session.refresh()`) to update object state

## References

- libtmux-mcp Documentation: https://libtmux-mcp.git-pull.com/
- libtmux Documentation: https://libtmux.git-pull.com/
- libtmux API Reference: https://libtmux.git-pull.com/api.html
- tmux man page: http://man.openbsd.org/OpenBSD-current/man1/tmux.1
- FastMCP: https://github.com/jlowin/fastmcp
- MCP Specification: https://modelcontextprotocol.io/

## Shipped vs. Branch-Internal Narrative

Long-running branches accumulate tactical decisions — renames,
refactors, attempts-then-reverts, intermediate states. Commit messages
and the diff hold *what changed* and *why*. Do not restate either in
artifacts the downstream reader holds: code, docstrings, README,
CHANGES, PR descriptions, release notes, migration guides.

When deciding what counts as branch-internal, use trunk or the parent
branch as the baseline — not intermediate states inside the current
branch.

**The Published-Release Test**

Before adding rename history, "previously" / "formerly" / "no longer
X" phrasing, "removed" / "moved" / "refactored" / "fixed" diff
paraphrases, or `### Fixes` entries to a user-facing surface, ask:

> Did users of the most recently published release ever experience
> this old name, old behavior, or bug?

If the answer is no, it is branch-internal narrative. Move it to the
commit message and describe only the current state in the artifact.

**Keep in shipped artifacts**

- Deprecations and migration guides for symbols that actually shipped.
- `### Fixes` entries for bugs that affected users of a published
  release.
- Comments explaining *why the current code looks this way* —
  invariants, platform quirks, upstream bug workarounds — that make
  sense to a reader who never saw the previous version.

**Default**: when in doubt, keep the artifact clean and put the story
in the commit.

### Cleanup in Hindsight

When applying this rule retroactively from inside a feature branch,
first establish scope by diffing against the parent branch (or trunk)
to identify which commits this branch actually introduced. Then:

- **Commits introduced in this branch** — prompt the user with two
  options: `fixup!` commits with `git rebase --autosquash` to address
  each causal commit at its source, or a single cleanup commit at
  branch tip. User chooses.
- **Commits already in trunk or a parent branch** — default to
  leaving them alone. Do not raise them as cleanup candidates; act
  only on explicit user instruction. If the user opts in, fold the
  cleanup into a single commit at branch tip and do not rewrite trunk
  or parent-branch history.
- **Scope guard** — if cleaning in-branch bleed would touch a
  colleague's in-flight work or expand the branch beyond its stated
  goal, default to staying in lane: protect the project's current
  goal, leave prior bleed alone, and don't introduce new bleed in the
  current change.
