(glossary)=

# Glossary

```{glossary}

MCP
    Model Context Protocol. A standard for AI agents to interact with tools and resources.

FastMCP
    A Python framework for building MCP servers. libtmux-mcp uses FastMCP to expose tmux operations as MCP tools.

libtmux
    A typed Python library that provides an ORM wrapper for tmux. libtmux-mcp depends on libtmux for all tmux interactions.

tmux
    A terminal multiplexer. It lets you switch easily between several programs in one terminal, detach them, and reattach them to a different terminal. See https://github.com/tmux/tmux.

Server
    A tmux server instance. Manages sessions and communicates via a socket.

Session
    A tmux session. Contains one or more windows. Has a name and ID (e.g. `$1`).

Window
    A tmux window within a session. Contains one or more panes. Has a name, index, and ID (e.g. `@1`).

Pane
    A tmux pane within a window. A pseudoterminal that runs a single process. Has an ID (e.g. `%1`) that is globally unique within a server.

Safety tier
    A level controlling which MCP tools are available: `readonly`, `mutating`, or `destructive`. Set via the {envvar}`LIBTMUX_SAFETY` env var.

Socket
    The Unix socket used to communicate with a tmux server. Can be specified by name (`-L`) or path (`-S`).

SIGINT
    Interrupt signal (Ctrl-C). Sent via {ref}`send-keys` with `keys: "C-c"` and `enter: false`. Most processes terminate gracefully on SIGINT.

SIGQUIT
    Quit signal (Ctrl-\\). Sent via {ref}`send-keys` with `keys: "C-\\"` and `enter: false`. Stronger than {term}`SIGINT` — may produce a core dump on Unix. Use as an escalation when SIGINT is ignored.
```
