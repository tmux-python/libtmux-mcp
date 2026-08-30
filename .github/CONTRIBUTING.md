# Contributing

Thanks for looking. libtmux-mcp is pre-1.0 and still settling — bug reports
with a reproduction, and notes on where the documentation misled you, are
the most useful contributions right now.

How this project writes prose — README, `CHANGES`, commit messages,
docstrings, source comments, and MCP tool descriptions — is set out
separately in [WRITING.md](WRITING.md). Read that before changing any of
it. The constraints every change is held to, and the map of what is
where, are in [AGENTS.md](../AGENTS.md).

## Getting set up

```console
$ uv sync --all-extras --dev
```

## The gates

Format:

```console
$ uv run ruff format .
```

Lint:

```console
$ uv run ruff check .
```

Auto-fix what ruff can fix on its own:

```console
$ uv run ruff check . --fix --show-fixes
```

Type-check:

```console
$ uv run mypy .
```

Test:

```console
$ uv run pytest
```

Documentation is a gate, not a courtesy. Examples in docstrings are
executed by `pytest` — the doctest flags live in `pyproject.toml`, so
there is no separate doctest step and a green `pytest` is the proof. Which
blocks qualify, and the one mistake that silently removes a test, are in
[WRITING.md](WRITING.md#documented-examples-that-run).

Before claiming a test or a gate works, show it failing. A gate that has
never been red is an assumption.

## Coding conventions

**Imports.** `from __future__ import annotations` at the top of every
file. `import typing as t` and access via namespace (`t.NamedTuple`, not
`from typing import NamedTuple`). Standard-library modules import by
namespace (`import pathlib`, not `from pathlib import Path`) —
third-party packages may use `from x import y`. `dataclasses` is the one
standard-library exception, for `from dataclasses import dataclass, field`.

**Logging.** `logging.getLogger(__name__)` in every module. Never
configure handlers, levels, or formatters in library code — that is the
calling application's job. Pass structured data via `extra=` on a log
call where it helps filtering, searching, or test assertions. Use lazy
`%`-style formatting (`logger.debug("msg %s", val)`), not an f-string —
the interpolation is skipped entirely when the level is filtered, and log
aggregators group by the template rather than by each rendered string.

**New APIs stay private** (a leading underscore, or simply unexported)
until a caller outside the defining module needs them.

## Tests

Tests use libtmux's pytest plugin fixtures (`server`, `session`, `window`,
`pane`), which create an isolated tmux session per test. MCP-specific
fixtures in `tests/conftest.py` — `mcp_server`, `mcp_session`,
`mcp_window`, `mcp_pane` — register that session in the MCP server cache
so tool functions can find it without environment variables. Reach for
these fixtures instead of `monkeypatch`/`MagicMock` wherever they apply;
document in the test docstring why an exceptional case needed a mock
instead.

Tests are standalone functions, not `class TestFoo:` groupings — use a
descriptive function name and file organization instead. Prefer `tmp_path`
over `tempfile`, and `monkeypatch` over `unittest.mock`, in the rare case a
mock is genuinely warranted.

An autouse fixture strips `TMUX`/`TMUX_PANE` from every test's
environment, so running the suite from inside a tmux session does not
leak the host pane into caller-identity checks; tests that want to
exercise the self-protection guards set those variables explicitly.
`addopts` reruns a failing test up to twice (`--reruns=2`) before it
counts as failed, and CI runs the suite under `pytest-xdist` — do not rely
on cross-test ordering or shared mutable module state.

Run tests continuously while developing:

```console
$ uv run ptw .
```

Doctests run on every pass already, because `--doctest-modules` is in
`addopts` — no extra flag is needed to include them.

**Debugging.** If a fix stalls into a loop of small guesses, stop and
strip back to the smallest reproduction before adding more debugging
code, and write down what you have ruled out. A change is not done while
it still carries experimental scaffolding or commented-out attempts.

## Documentation

Build:

```console
$ just build-docs
```

Serve with auto-reload:

```console
$ just start-docs
```

Tool pages under `docs/tools/` render their schema live from the
`{fastmcp-tool}` directive at build time, so a docstring or `Field`
description edited in `src/` shows up the next time the docs build — the
page's hand-written prose (`Use when`, `Avoid when`, examples) is the only
part you edit directly. See
[WRITING.md](WRITING.md#documentation-site-voice) for how to write it.

## Releasing

Never create tags. Never push tags. The owner handles tagging and tag
pushes, because a tag triggers the publish workflow. See [Release
commits](WRITING.md#release-commits).

For reference, the owner's release process: update `CHANGES`, bump
`__version__` in `src/libtmux_mcp/__about__.py`, commit, then tag and push
the tag — which triggers the CI workflow that builds the package, attests
it, and publishes to PyPI via OIDC trusted publishing.

## Pull requests

One subject per pull request. Unrelated cleanup found along the way
belongs in its own commit, and usually in its own pull request.

Discuss a substantial change via an issue before making it.

Commit format is in [WRITING.md](WRITING.md#commits).

You may merge your own pull request once you have the sign-off of one
other developer. If you do not have merge permission, request a reviewer
to merge it for you.

Applying the [slop prevention](WRITING.md#slop-prevention) rules to
history that already landed on trunk is a separate, deliberate act, not a
side effect of an unrelated change: default to leaving it alone, and act
on a maintainer's explicit request, scoped to a single cleanup commit or a
`git rebase --autosquash` pass — never rewriting shared history beyond
that.

## Decorum

- Participants will be tolerant of opposing views.
- Participants must ensure that their language and actions are free of
  personal attacks and disparaging personal remarks.
- When interpreting the words and actions of others, participants should
  always assume good intentions.
- Behaviour which can be reasonably considered harassment will not be
  tolerated.

Based on [Ruby's Community Conduct
Guideline](https://www.ruby-lang.org/en/conduct/).

## Security

This repository has no `SECURITY.md`. Please do not open a public issue
for a vulnerability — use GitHub's private vulnerability reporting
(the repository's Security tab, "Report a vulnerability") instead.
