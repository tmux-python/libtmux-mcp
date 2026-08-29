# justfile for libtmux-mcp
# https://just.systems/

set shell := ["bash", "-uc"]

# File patterns
py_files := "find . -type f -not -path '*/\\.*' | grep -i '.*[.]py$' 2> /dev/null"
doc_files := "find . -type f -not -path '*/\\.*' | grep -i '.*[.]rst$\\|.*[.]md$\\|.*[.]css$\\|.*[.]py$\\|mkdocs\\.yml\\|CHANGES\\|TODO\\|.*conf\\.py' 2> /dev/null"
all_files := "find . -type f -not -path '*/\\.*' | grep -i '.*[.]py$\\|.*[.]rst$\\|.*[.]md$\\|.*[.]css$\\|.*[.]py$\\|mkdocs\\.yml\\|CHANGES\\|TODO\\|.*conf\\.py' 2> /dev/null"

# List all available commands
default:
    @just --list

# Run tests with pytest
[group: 'test']
test *args:
    uv run py.test {{ args }}

# Run tests then start continuous testing with pytest-watcher
[group: 'test']
start:
    just test
    uv run ptw .

# Watch files and run tests on change (requires entr)
[group: 'test']
watch-test:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v entr > /dev/null; then
        {{ all_files }} | entr -c just test
    else
        just test
        just _entr-warn
    fi

# Build documentation
[group: 'docs']
build-docs:
    just -f docs/justfile html

# Watch files and rebuild docs on change
[group: 'docs']
watch-docs:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v entr > /dev/null; then
        {{ doc_files }} | entr -c just build-docs
    else
        just build-docs
        just _entr-warn
    fi

# Serve documentation
[group: 'docs']
serve-docs:
    just -f docs/justfile serve

# Watch and serve docs simultaneously
[group: 'docs']
dev-docs:
    #!/usr/bin/env bash
    set -euo pipefail
    just watch-docs &
    just serve-docs

# Start documentation server with auto-reload
[group: 'docs']
start-docs:
    just -f docs/justfile start

# Start documentation design mode (watches static files)
[group: 'docs']
design-docs:
    just -f docs/justfile design

# Format code with ruff
[group: 'lint']
ruff-format:
    uv run ruff format .

# Run ruff linter
[group: 'lint']
ruff:
    uv run ruff check .

# Run the lint gates exactly as CI runs them.
#
# `ruff-format` rewrites files and `mypy` takes a find-derived file list, so
# neither can reproduce a CI lint failure locally: the first fixes what CI
# rejects, the second checks a different set than `mypy .` does.
[group: 'lint']
lint-ci:
    uv run ruff check .
    uv run ruff format . --check
    uv run mypy .

# Assert the package imports with dev dependencies absent, as CI does.
#
# A runtime module that imports pytest, ruff or mypy is invisible to every
# other gate -- they all run where those are present - and fails CI at its
# earliest step.
#
# The throwaway UV_PROJECT_ENVIRONMENT is what makes this real. CI runs its
# check BEFORE installing dependencies, so `--no-dev` there has nothing to
# fall back to; locally it reuses .venv, which already has the dev packages,
# and the check passes without ever testing anything. The recipe asserts the
# environment is actually dev-free rather than trusting the flag.
[group: 'lint']
deps-ci:
    #!/usr/bin/env bash
    set -euo pipefail
    scratch=$(mktemp -d)
    trap 'rm -rf "$scratch"' EXIT
    env -u VIRTUAL_ENV UV_PROJECT_ENVIRONMENT="$scratch/venv" \
        uv run --no-dev -- python -c '
    import importlib
    for dev in ("pytest", "ruff", "mypy"):
        try:
            importlib.import_module(dev)
        except ImportError:
            continue
        raise SystemExit(f"{dev} is importable; this check is not dev-free")
    from libtmux_mcp import main
    from libtmux_mcp.__about__ import __version__
    print("libtmux-mcp version:", __version__, "(dev-free)")'

# Run the suite the way CI runs it: under xdist, with coverage.
#
# `just test` is serial, so a test that depends on ordering or shared state
# passes it and fails CI. Coverage is included because COV_CORE_* changes
# process startup, which is itself a difference worth reproducing.
[group: 'test']
test-ci:
    uv run py.test --cov=./ --cov-append --cov-report=xml -n auto --verbose

# Watch files and run ruff on change
[group: 'lint']
watch-ruff:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v entr > /dev/null; then
        {{ py_files }} | entr -c just ruff
    else
        just ruff
        just _entr-warn
    fi

# Run mypy type checker
[group: 'lint']
mypy:
    uv run mypy $({{ py_files }})

# Watch files and run mypy on change
[group: 'lint']
watch-mypy:
    #!/usr/bin/env bash
    set -euo pipefail
    if command -v entr > /dev/null; then
        {{ py_files }} | entr -c just mypy
    else
        just mypy
        just _entr-warn
    fi

# Format markdown files with prettier
[group: 'format']
format-markdown:
    prettier --parser=markdown -w *.md docs/*.md docs/**/*.md CHANGES

# Detect which agent CLIs exist on this machine
[group: 'mcp']
mcp-detect:
    uv run scripts/mcp_swap.py detect

# Show how each detected CLI resolves this MCP server today
[group: 'mcp']
mcp-status *args:
    uv run scripts/mcp_swap.py status {{ args }}

# Rewrite each detected CLI's config to run this checkout (editable)
[group: 'mcp']
mcp-use-local *args:
    uv run scripts/mcp_swap.py use-local {{ args }}

# Restore each CLI's config from the backup written by mcp-use-local
[group: 'mcp']
mcp-revert *args:
    uv run scripts/mcp_swap.py revert {{ args }}

[private]
_entr-warn:
    @echo "----------------------------------------------------------"
    @echo "     ! File watching functionality non-operational !      "
    @echo "                                                          "
    @echo "Install entr(1) to automatically run tasks on file change."
    @echo "See https://eradman.com/entrproject/                      "
    @echo "----------------------------------------------------------"
