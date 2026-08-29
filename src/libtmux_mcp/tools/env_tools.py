"""MCP tools for tmux environment variable management."""

from __future__ import annotations

import typing as t

from libtmux_mcp._utils import (
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    TAG_MUTATING,
    TAG_READONLY,
    _get_server,
    _raise_if_not_env_name,
    _resolve_session,
    handle_tool_errors,
)
from libtmux_mcp.models import EnvironmentResult, EnvironmentSetResult

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


@handle_tool_errors
def show_environment(
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> EnvironmentResult:
    """Show tmux environment variables.

    Use to inspect tmux environment variables that affect child processes.

    Parameters
    ----------
    session_name : str, optional
        Session name to query environment for.
    session_id : str, optional
        Session ID to query environment for.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    EnvironmentResult
        Environment variable mapping.
    """
    server = _get_server(socket_name=socket_name)

    if session_name is not None or session_id is not None:
        session = _resolve_session(
            server,
            session_name=session_name,
            session_id=session_id,
        )
        env_dict = session.show_environment()
    else:
        env_dict = server.show_environment()

    # tmux prints a variable marked REMOVED as ``-NAME``
    # (cmd-show-environment.c). libtmux keeps the dash in the key and
    # gives it the value True, so the removal was encoded in the key --
    # ``variables["KRB5CCNAME"]`` raised KeyError while
    # ``variables["-KRB5CCNAME"]`` answered True, reading as "set to
    # true" for a variable that is explicitly unset. Split the two
    # apart instead, so a value is always a value.
    variables: dict[str, str] = {}
    removed: list[str] = []
    for name, value in env_dict.items():
        if name.startswith("-"):
            removed.append(name[1:])
        elif isinstance(value, str):
            variables[name] = value
    scope = (
        "session" if (session_name is not None or session_id is not None) else "global"
    )
    return EnvironmentResult(
        scope_queried=scope, variables=variables, removed=sorted(removed)
    )


@handle_tool_errors
def set_environment(
    name: str,
    value: str,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> EnvironmentSetResult:
    """Set a tmux environment variable.

    Use to set variables that will be inherited by new panes and windows.
    Changes do not affect already-running processes.

    .. warning::
       Values set here propagate into **every** shell tmux later spawns
       in the targeted scope — including panes the user opens manually,
       not just panes the agent drives. A caller that writes ``PATH``,
       ``LD_PRELOAD``, or ``AWS_*`` variables can influence future
       commands the human user types directly. Treat this as
       elevated-risk within the ``mutating`` safety tier. The audit log
       redacts the ``value`` argument, but the side effects persist on
       disk/memory until tmux is restarted. Prefer ``env VAR=value
       command`` via :func:`~libtmux_mcp.tools.pane_tools.send_keys`
       when you only need the override for a single command. See
       :doc:`/topics/safety`.

    Parameters
    ----------
    name : str
        Environment variable name.
    value : str
        Environment variable value.
    session_name : str, optional
        Session name to set environment for.
    session_id : str, optional
        Session ID to set environment for.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    EnvironmentSetResult
        Confirmation with variable name, value, and status.
    """
    _raise_if_not_env_name(name)
    server = _get_server(socket_name=socket_name)

    if session_name is not None or session_id is not None:
        session = _resolve_session(
            server,
            session_name=session_name,
            session_id=session_id,
        )
        session.set_environment(name, value)
    else:
        server.set_environment(name, value)

    return EnvironmentSetResult(name=name, value=value, status="set")


@handle_tool_errors
def unset_environment(
    name: str,
    session_name: str | None = None,
    session_id: str | None = None,
    socket_name: str | None = None,
) -> EnvironmentSetResult:
    """Remove a tmux environment variable.

    The counterpart to :func:`set_environment`. Setting a variable to
    the empty string is not the same thing: tmux keeps the name with an
    empty value, and new panes still inherit it as set-but-empty.

    Parameters
    ----------
    name : str
        Environment variable name to remove.
    session_name : str, optional
        Session name to remove the variable from.
    session_id : str, optional
        Session ID to remove the variable from.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    EnvironmentSetResult
        Confirmation with the variable name and ``status="unset"``.
        ``value`` is null: the variable no longer has one.
    """
    _raise_if_not_env_name(name)
    server = _get_server(socket_name=socket_name)

    target: t.Any = server
    if session_name is not None or session_id is not None:
        target = _resolve_session(
            server,
            session_name=session_name,
            session_id=session_id,
        )

    # tmux exits 0 whether or not the variable existed, so removing
    # something and removing nothing are indistinguishable afterwards.
    # Read first: "unset" and "absent" call for different reactions when
    # a caller is reconciling state.
    existed = name in target.show_environment()
    target.unset_environment(name)

    return EnvironmentSetResult(
        name=name, value=None, status="unset" if existed else "absent"
    )


def register(mcp: FastMCP) -> None:
    """Register environment tools with the MCP instance."""
    mcp.tool(
        title="Show tmux Environment",
        annotations=ANNOTATIONS_RO,
        tags={TAG_READONLY},
    )(show_environment)
    mcp.tool(
        title="Set tmux Environment",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(set_environment)
    mcp.tool(
        title="Unset tmux Environment",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(unset_environment)
