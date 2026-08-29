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

Several tests poll with libtmux's `retry_until`, whose budget is
`RETRY_TIMEOUT_SECONDS` (default 8 s). That is a ceiling, not a spend:
measured, the polls it guards complete in 1.4–4.3 s, so there is
normally 2–6x of margin and raising it changes nothing on the passing
path.

On a heavily loaded machine the margin can close, and the symptom is
`libtmux.exc.WaitTimeout` at just over 10 s. Measured at loadavg 213 on
a 20-core box, roughly one run in six tipped one test — never the same
one twice, because whichever wait-bounded test gets starved is the one
that fails. CI runs at far lower parallelism and has not shown it.

If you see it, widen the ceiling:

```console
$ RETRY_TIMEOUT_SECONDS=20 uv run pytest -n auto
```

The variable has to be set before pytest starts: libtmux binds it as a
default argument when `retry_until` is defined, and its pytest plugin
imports that module before any `conftest.py` runs.

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
