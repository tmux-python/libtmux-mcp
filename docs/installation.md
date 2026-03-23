(installation)=

# Installation

## Requirements

- Python 3.10+
- tmux >= 3.2a
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Recommended: uvx

`uvx` handles install, deps, and execution in one step — no persistent install needed:

```console
$ uvx libtmux-mcp
```

To wire it into your MCP client, see {ref}`clients`.

## pip / uv pip

```console
$ uv pip install libtmux-mcp
```

```console
$ pip install libtmux-mcp
```

## Development install

Clone and install in editable mode:

```console
$ git clone https://github.com/tmux-python/libtmux-mcp.git
```

```console
$ cd libtmux-mcp
```

```console
$ uv pip install -e "."
```

Code changes take effect immediately — no reinstall needed.

## Running the server

```console
$ libtmux-mcp
```

Or via Python module:

```console
$ python -m libtmux_mcp
```

## Upgrading

```console
$ uv pip install --upgrade libtmux-mcp
```

With `uvx`, you always get the latest version automatically.
