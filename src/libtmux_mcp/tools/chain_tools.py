"""MCP tools that run a chain of tmux commands as one native invocation.

These leverage libtmux's experimental ``libtmux._experimental.chain`` API to
fold an ordered set of tmux commands into a single ``tmux a ; b ; c`` dispatch
(one subprocess), instead of issuing one tmux call per command. The chain API is
experimental and pinned to a sibling worktree; do not ship to a release.
"""

from __future__ import annotations

import asyncio
import typing as t

from libtmux._experimental.chain import (
    AsyncServerPlanRunner,
    CommandCall,
    CommandChain,
    ForwardPlan,
)

from libtmux_mcp._utils import (
    ANNOTATIONS_DESTRUCTIVE,
    ANNOTATIONS_SHELL,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    ChainCommand,
    ForwardLayoutResult,
    ForwardSplit,
    RunCommandChainResult,
)

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


def _send_keys_decorate(keys: str) -> t.Callable[..., t.Any]:
    """Build a send_keys decorate bound to a captured string (per-iteration binding)."""

    def build(handle: t.Any) -> t.Any:
        return handle.cmd.send_keys(keys, enter=True)

    return build


@handle_tool_errors_async
async def build_forward_layout(
    splits: list[ForwardSplit],
    pane_id: str | None = None,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> ForwardLayoutResult:
    r"""Split a seed pane into several panes, returning their new ids.

    Unlike a single ``\;`` chain, this captures the id tmux assigns each new
    pane (a fresh id can't be substituted back into the same invocation), so it
    resolves over the minimum number of dispatches: a single split folds into
    one, several independent splits take one per creation plus one trailing
    chain for the decorations.

    Parameters
    ----------
    splits : list[ForwardSplit]
        Splits off the seed pane, each ``{horizontal, shell, send_keys}``.
    pane_id : str, optional
        Seed pane id; defaults to the resolved/active pane.
    session_name, session_id : str, optional
        Used to resolve the seed pane when ``pane_id`` is omitted.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    ForwardLayoutResult
        The created pane ids (in split order) and the dispatch count.
    """
    if not splits:
        msg = "splits must not be empty"
        raise ExpectedToolError(msg)

    server = _get_server(socket_name=socket_name)
    seed = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
    )
    plan = ForwardPlan.from_pane(seed)
    for split in splits:
        handle = plan.split(horizontal=split.horizontal, shell=split.shell)
        if split.send_keys is not None:
            handle.do(_send_keys_decorate(split.send_keys))

    resolved = await plan.run_resolving_async(AsyncServerPlanRunner(server))
    pane_ids = [resolved.bindings[index] for index in range(len(splits))]
    return ForwardLayoutResult(
        pane_ids=pane_ids,
        dispatch_count=len(resolved.results),
    )


def register(mcp: FastMCP) -> None:
    """Register chain tools with the MCP instance."""
    mcp.tool(
        title="Run Command Chain",
        annotations=ANNOTATIONS_DESTRUCTIVE,
        tags={TAG_DESTRUCTIVE},
    )(run_command_chain)
    mcp.tool(
        title="Build Forward Layout",
        annotations=ANNOTATIONS_SHELL,
        tags={TAG_MUTATING},
    )(build_forward_layout)
