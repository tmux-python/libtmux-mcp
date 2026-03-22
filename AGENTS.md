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
- 25 MCP tools across 6 modules: server, session, window, pane, options, environment
- 6 `tmux://` URI resources for browsing tmux hierarchy
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

**All functions and methods MUST have working doctests.** Doctests serve as both documentation and tests.

**CRITICAL RULES:**
- Doctests MUST actually execute - never comment out function calls or similar
- Doctests MUST NOT be converted to `.. code-block::` as a workaround (code-blocks don't run)
- If you cannot create a working doctest, **STOP and ask for help**

**`# doctest: +SKIP` is NOT permitted** - it's just another workaround that doesn't test anything. Use the fixtures properly - tmux is required to run tests anyway.

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

**Split long commands with `\` for readability.**

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
