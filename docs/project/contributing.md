# Development

Install [git] and [uv] ([install](https://docs.astral.sh/uv/getting-started/installation/))

[git]: https://git-scm.com/
[uv]: https://github.com/astral-sh/uv

Clone:

```console
$ git clone https://github.com/tmux-python/libtmux-mcp.git
```

```console
$ cd libtmux-mcp
```

Install:

```console
$ uv pip install -e . -G dev
```

## Testing

```console
$ uv run pytest
```

Run a specific test file:

```console
$ uv run pytest tests/test_pane_tools.py
```

Run a specific test:

```console
$ uv run pytest tests/test_pane_tools.py::test_send_keys
```

Watch mode:

```console
$ uv run ptw .
```

### Reading a green run

`--reruns=2` is in `addopts`, so a passing summary means "did not fail
three times consecutively" rather than "did not fail". Each retry now
prints its own `RERUN <nodeid>` line, so an absorbed failure names
itself — but the summary counts still say `passed`.

When hunting a flake, turn retries off so the first failure is the
reported one:

```console
$ uv run pytest -n auto --reruns 0
```

### Timeouts under heavy parallel load

Many tests poll with libtmux's `retry_until`. On a heavily loaded
machine one can exceed its budget, and the symptom is
`libtmux.exc.WaitTimeout` at just over 10 s.

There is no knob for it. libtmux exposes `RETRY_TIMEOUT_SECONDS`, but
it is inert here: all 77 `retry_until` call sites in this suite pass a
timeout explicitly — 73 of them the literal `10` — so none reads the
environment variable. Counted by walking the AST, because a regex over
these calls miscounts: the predicate is usually a lambda containing its
own parentheses.

The bound is a ceiling rather than a spend — the polls it guards
complete in 1.4–4.3 s — so there is normally 2–7x of margin. Measured
at loadavg 213 on a 20-core box, roughly one run in six tipped one
test, and never the same one twice: whichever wait-bounded test gets
starved is the one that fails. CI runs at far lower parallelism and has
not shown it.

If you hit one, re-run it in isolation before treating it as a defect.

## Linting

```console
$ uv run ruff check .
```

Format:

```console
$ uv run ruff format .
```

Auto-fix:

```console
$ uv run ruff check . --fix --show-fixes
```

## Type checking

```console
$ uv run mypy
```

## Documentation

Build:

```console
$ just build-docs
```

Serve with auto-reload:

```console
$ just start-docs
```

## Workflow

1. Format: `uv run ruff format .`
2. Test: `uv run pytest`
3. Lint: `uv run ruff check . --fix --show-fixes`
4. Types: `uv run mypy`
5. Verify: `uv run pytest`

## Releasing

Releases are published to PyPI via GitHub Actions when a tag is pushed:

```console
$ git tag v0.1.0
```

```console
$ git push --tags
```

The CI workflow builds the package, creates attestations, and publishes via OIDC trusted publishing.
