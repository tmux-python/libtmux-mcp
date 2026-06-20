"""MCP tools that run a chain of tmux commands as one native invocation.

These leverage libtmux's experimental ``libtmux._experimental.chain`` API to
fold an ordered set of tmux commands into a single ``tmux a ; b ; c`` dispatch
(one subprocess), instead of issuing one tmux call per command. The chain API is
experimental and pinned to a sibling worktree; do not ship to a release.
"""

from __future__ import annotations

import asyncio
import typing as t

from libtmux._experimental.chain import CommandCall, CommandChain

from libtmux_mcp._utils import (
    ANNOTATIONS_DESTRUCTIVE,
    TAG_DESTRUCTIVE,
    ExpectedToolError,
    _get_server,
    handle_tool_errors_async,
)
from libtmux_mcp.models import ChainCommand, RunCommandChainResult

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

#: tmux commands refused outright: unrecoverable, no ``-t`` makes them safe, and
#: they would take down the server hosting this MCP.
_BLOCKED_COMMANDS = frozenset({"kill-server"})


@handle_tool_errors_async
async def run_command_chain(
    commands: list[ChainCommand],
    socket_name: str | None = None,
) -> RunCommandChainResult:
    """Run an ordered list of tmux commands as ONE native tmux invocation.

    The commands are folded into a single ``tmux a ; b ; c`` sequence and
    dispatched once, instead of one tmux subprocess per command. tmux applies
    its native sequence semantics: a command that errors aborts the rest. Each
    command's ``-t`` target is passed through verbatim, so a chain may span
    heterogeneous scopes (panes, windows, sessions).

    Parameters
    ----------
    commands : list[ChainCommand]
        Ordered tmux commands, each ``{command, args, target}``. Must be
        non-empty; ``kill-server`` is refused.
    socket_name : str, optional
        tmux socket name (falls back to ``LIBTMUX_SOCKET``).

    Returns
    -------
    RunCommandChainResult
        The rendered ``argv`` (with ``;`` separators), the command count, and
        the single invocation's merged exit code, stdout, and stderr.
    """
    if not commands:
        msg = "commands must not be empty"
        raise ExpectedToolError(msg)

    blocked = sorted({cmd.command for cmd in commands} & _BLOCKED_COMMANDS)
    if blocked:
        msg = f"refusing to run unrecoverable command(s): {', '.join(blocked)}"
        raise ExpectedToolError(msg)

    server = _get_server(socket_name=socket_name)
    try:
        chain = CommandChain(
            tuple(
                CommandCall(cmd.command, tuple(cmd.args), target=cmd.target)
                for cmd in commands
            ),
        )
    except ValueError as exc:  # empty-string target / empty chain (fail closed)
        raise ExpectedToolError(str(exc)) from exc

    argv = list(chain.argv())
    # A live Server satisfies the CommandRunner protocol; dispatch ONCE, off the
    # event loop (libtmux dispatch is blocking).
    result = await asyncio.to_thread(chain.run, server)
    return RunCommandChainResult(
        argv=argv,
        command_count=len(commands),
        returncode=result.returncode,
        stdout=list(result.stdout),
        stderr=list(result.stderr),
    )


def register(mcp: FastMCP) -> None:
    """Register chain tools with the MCP instance."""
    mcp.tool(
        title="Run Command Chain",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(run_command_chain)
