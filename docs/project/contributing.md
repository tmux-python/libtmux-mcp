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
