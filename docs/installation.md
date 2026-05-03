(installation)=

# Installation

## Requirements

- Python 3.10+
- tmux >= 3.2a
- [uv](https://github.com/astral-sh/uv) ([install](https://docs.astral.sh/uv/getting-started/installation/)) or [pipx](https://github.com/pypa/pipx) ([install](https://pipx.pypa.io/stable/installation/)) — for running without a persistent install

## Run without installing

No persistent install needed — run directly with a package executor:

`````{tab} uvx
```console
$ uvx libtmux-mcp
```
`````

`````{tab} pipx
```console
$ pipx run libtmux-mcp
```
`````

To wire it into your MCP client, see {ref}`clients`.

## Install the package

`````{tab} uv
```console
$ uv pip install libtmux-mcp
```
`````

`````{tab} pip
```console
$ pip install libtmux-mcp
```
`````

## Development install

Install [uv](https://github.com/astral-sh/uv) ([install](https://docs.astral.sh/uv/getting-started/installation/)), then clone and install in editable mode:

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

`````{tab} uv
```console
$ uv pip install --upgrade libtmux-mcp
```
`````

`````{tab} pip
```console
$ pip install --upgrade libtmux-mcp
```
`````

With `uvx` or `pipx run`, you always get the latest version automatically.

:::{tip}
If `uvx` fails to resolve dependencies or installs an unexpectedly old version, you may be hitting a cached index. See {ref}`troubleshooting` for `uv` cache and `--exclude-newer` workarounds.
:::
