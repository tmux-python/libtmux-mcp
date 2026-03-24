(releasing)=

# Releasing

## Version scheme

libtmux-mcp follows [PEP 440](https://peps.python.org/pep-0440/) with alpha suffixes during pre-1.0 development (e.g. `0.1.0a0`).

## Release checklist

1. Update `CHANGES`.
2. Bump version in `src/libtmux_mcp/__about__.py`.
3. Commit and tag: `git tag v0.X.Y`.
4. Push with tags: `git push --follow-tags`.
5. CI publishes to PyPI.
