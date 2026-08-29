(installation)=

# Installation

## Requirements

- Python 3.10+
- tmux >= 3.2a — and see {ref}`client-server-versions` if the tmux running
  this server is older than the tmux that started the session you point it at
- [uv](https://github.com/astral-sh/uv) ([install](https://docs.astral.sh/uv/getting-started/installation/)) or [pipx](https://github.com/pypa/pipx) ([install](https://pipx.pypa.io/stable/installation/)) — for running without a persistent install

(client-server-versions)=

## Client and server tmux versions

The floor above is the tmux **this server runs**. There is a second
constraint, because tmux sockets outlive the binary that made them: a
client can only talk to a server whose protocol it understands.

Measured across every pair of the nine supported versions — 81
combinations, each checked against the tmux binary directly and then
through this server:

    server 3.6 and newer, client 3.5 and older   unreachable
    every other pair                             fine

A clean rectangle, and one-directional: 3.6+ clients read every server
including old ones, and everything 3.5-and-older is mutually compatible
both ways.

You hit this when a system tmux upgrade leaves a session running under
the old binary, or when this server runs an older tmux than the one
that created the socket. It surfaces honestly rather than as an empty
result:

    tmux server exists but could not be queried: server exited
    unexpectedly. ...

That is the start of the message, not all of it — the rest explains why
reporting no sessions would be wrong. Grep for `could not be queried`.

If you see that, compare `tmux -V` against the version that started the
session and point `LIBTMUX_TMUX_BIN` at the matching binary.

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
