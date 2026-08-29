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
    include_inherited: bool = False,
    socket_name: str | None = None,
) -> EnvironmentResult:
    """Show tmux environment variables.

    Use to inspect tmux environment variables that affect child processes.

    Naming a session reads only what is set ON that session. A new pane
    there also inherits the GLOBAL environment, so the session view
    alone does not answer "what will the next pane get" --
    ``include_inherited=True`` merges the global set underneath and does.
    Omitting the session reads the global set on its own.

    Same shape as ``show_option`` and ``show_hook``: an answer at one
    scope is not an answer about what is in force.

    Parameters
    ----------
    session_name : str, optional
        Session name to query environment for.
    session_id : str, optional
        Session ID to query environment for.
    include_inherited : bool
        Merge the global environment underneath the session's, so the
        result is what a new pane in that session actually receives.
        Session values win. Ignored when no session is named, since the
        global set has nothing to inherit from.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    EnvironmentResult
        Environment variable mapping.
    """
    server = _get_server(socket_name=socket_name)

    scoped = session_name is not None or session_id is not None
    if scoped:
        session = _resolve_session(
            server,
            session_name=session_name,
            session_id=session_id,
        )
        env_dict = session.show_environment()
        if include_inherited:
            # Global first so the session's own values overwrite it --
            # that is the precedence a spawned pane sees.
            env_dict = {**server.show_environment(), **env_dict}
    else:
        env_dict = server.show_environment()

    # tmux prints a REMOVED variable as ``-NAME``, and libtmux keeps the
    # dash in the key with the value True -- so the removal is encoded in
    # the key, and ``variables["-KRB5CCNAME"]`` reads as "set to true" for
    # a variable that is explicitly unset. Split apart, a value is always
    # a value.
    variables: dict[str, str] = {}
    removed: list[str] = []
    for name, value in env_dict.items():
        if name.startswith("-"):
            removed.append(name[1:])
        elif isinstance(value, str):
            variables[name] = value
    return EnvironmentResult(
        scope_queried="session" if scoped else "global",
        variables=variables,
        removed=sorted(removed),
        include_inherited=include_inherited and scoped,
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

    Notes
    -----
    ``status="absent"`` means nothing was removed AT THE SCOPE TARGETED,
    which covers two situations with opposite consequences: the name
    never existed, or it is set GLOBALLY and every new pane still
    receives it. ``still_set_globally`` separates them.
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

    # "Nothing to remove here" and "still in force everywhere" were the
    # same answer. Measured: unsetting a globally-set name at session
    # scope reported absent, and a pane spawned afterwards still got it.
    still_global = (
        target is not server and not existed and name in server.show_environment()
    )
    return EnvironmentSetResult(
        name=name,
        value=None,
        status="unset" if existed else "absent",
        still_set_globally=still_global,
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
