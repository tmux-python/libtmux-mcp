(code-style)=

# Code style

## Linting and formatting

libtmux-mcp uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```console
$ uv run ruff check .
```

```console
$ uv run ruff format .
```

## Type checking

[mypy](https://mypy-lang.org/) with strict mode:

```console
$ uv run mypy
```

## Docstrings

NumPy-style docstrings throughout.

## Imports

- `from __future__ import annotations` at the top of every file.
- `import typing as t` and access via namespace.
- Standard-library modules imported by namespace (`import pathlib`, not `from pathlib import Path`).
