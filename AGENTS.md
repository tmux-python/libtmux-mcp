# AGENTS.md

libtmux-mcp is a Model Context Protocol server that gives an AI agent
programmatic control over tmux — sessions, windows, panes — built on
[libtmux](https://libtmux.git-pull.com/).

Follow the conventions already in the tree, and keep a change scoped to
what was asked for.

## What is here

| Path | What it is |
| ---- | ---------- |
| `src/libtmux_mcp/server.py` | FastMCP instance: construction, instructions, lifespan |
| `src/libtmux_mcp/middleware.py` | Safety-tier gating, response limiting, error mapping |
| `src/libtmux_mcp/_utils.py` | Server cache, object resolvers/serializers, `handle_tool_errors` |
| `src/libtmux_mcp/models.py` | Pydantic models for tool outputs |
| `src/libtmux_mcp/tools/` | MCP tool implementations: one module per tmux object, plus batch/buffer/hook/wait_for |
| `src/libtmux_mcp/resources/` | `tmux://` URI resources for browsing the hierarchy |
| `src/libtmux_mcp/prompts/` | MCP prompt templates |
| `scripts/mcp_swap.py` | Dev script: point agent CLI configs at a local checkout |
| `tests/` | pytest suite; `tests/docs/` covers doc-widget and topic contracts |
| `docs/` | Sphinx source; `docs/tools/` mirrors `tools/`, one page per tool |
| `CHANGES` | Changelog, included into the built `docs/history.md` page |
| `conftest.py` | Root doctest fixtures — see `.github/WRITING.md` |

## Which policy applies

- Documentation, user-facing text, `CHANGES`, release notes, commit
  messages, docstrings, and source comments:
  [.github/WRITING.md](.github/WRITING.md)
- Environment, the gates, tests, documentation builds, releases, and pull
  requests: [.github/CONTRIBUTING.md](.github/CONTRIBUTING.md)

Each of those is the single home for its subject. Where a rule seems to
be stated twice, the file listed above is the one that governs.

## Change discipline

- Make the smallest coherent change that solves the verified problem;
  keep unrelated cleanup out of it.
- Reuse an existing file, helper, API, or test before adding a new one.
- Add a file only for a durable boundary — a distinct responsibility,
  independent reuse, or splitting an oversized module — not for a
  single-use helper or a one-line re-export.
- Add a test for every user-visible behaviour change, and a `CHANGES`
  entry for every change to a tool, resource, prompt, or configuration
  variable.
- A passing gate is evidence only once it has been shown capable of
  failing. Pair a new test with a deliberate break that proves it bites.

Tools are grouped into the unordered toolsets `inspect`, `manage`,
`execute`, and `teardown`; `LIBTMUX_TOOLSETS` selects which are exposed
(default `inspect,manage,execute`), and the middleware refuses any tool
carrying none of them. Filtering shapes what is advertised, not what a
pane can run. All tmux access goes through
libtmux's `cmd()` on `Server`/`Session`/`Window`/`Pane`, returning a
`CommandResult` with `stdout`/`stderr`; a libtmux object can go stale when
tmux state changes externally, so call `.refresh()` before trusting a
cached attribute. Server-level MCP instructions are capped at 2048 bytes
and the server enforces that at startup.

## References

- Docs: <https://libtmux-mcp.git-pull.com/>
- libtmux docs: <https://libtmux.git-pull.com/>
- MCP specification: <https://modelcontextprotocol.io/>
- Issues: <https://github.com/tmux-python/libtmux-mcp/issues>
