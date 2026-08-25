"""MCP tools for tmux option management."""

from __future__ import annotations

import typing as t

from libtmux.constants import OptionScope

from libtmux_mcp._utils import (
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    TAG_MUTATING,
    TAG_READONLY,
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    handle_tool_errors,
)
from libtmux_mcp.models import OptionResult, OptionSetResult

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from libtmux.options import OptionsMixin

_SCOPE_MAP: dict[str, OptionScope] = {
    "server": OptionScope.Server,
    "session": OptionScope.Session,
    "window": OptionScope.Window,
    "pane": OptionScope.Pane,
}


def _resolve_option_target(
    socket_name: str | None,
    scope: t.Literal["server", "session", "window", "pane"] | None,
    target: str | None,
) -> tuple[OptionsMixin, OptionScope | None]:
    """Resolve the target object and scope for option operations."""
    server = _get_server(socket_name=socket_name)
    opt_scope = _SCOPE_MAP.get(scope) if scope is not None else None

    if scope is not None and opt_scope is None:
        valid = ", ".join(sorted(_SCOPE_MAP))
        msg = f"Invalid scope: {scope!r}. Valid: {valid}"
        raise ExpectedToolError(msg)

    if target is not None and opt_scope is None:
        msg = "scope is required when target is specified"
        raise ExpectedToolError(msg)

    if target is not None and opt_scope is not None:
        if opt_scope == OptionScope.Session:
            return _resolve_session(server, session_name=target), opt_scope
        if opt_scope == OptionScope.Window:
            return _resolve_window(server, window_id=target), opt_scope
        if opt_scope == OptionScope.Pane:
            return _resolve_pane(server, pane_id=target), opt_scope
    return server, opt_scope


@handle_tool_errors
def show_option(
    option: str,
    scope: t.Literal["server", "session", "window", "pane"] | None = None,
    target: str | None = None,
    global_: bool = False,
    include_inherited: bool = False,
    socket_name: str | None = None,
) -> OptionResult:
    """Show a tmux option value.

    Use to check tmux configuration values such as history-limit,
    mouse support, or status bar settings.

    **``value: null`` means "not set AT THIS SCOPE", not "not set".**
    An option inherited from a wider scope reads as null here, so
    ``show_option("history-limit", scope="session")`` answers null while
    50000 is in force. Pass ``include_inherited=True`` (tmux's ``-A``)
    to ask what is actually in effect, which is what most questions —
    "is mouse mode on?" — really mean.

    Parameters
    ----------
    option : str
        The tmux option name to query.
    scope : str, optional
        Option scope.
    target : str, optional
        Target identifier. For session scope: session name
        (e.g. 'mysession'). For window scope: window ID (e.g. '@1').
        For pane scope: pane ID (e.g. '%1'). Requires scope.
    global_ : bool
        Whether to query the global option.
    include_inherited : bool
        Resolve inherited values (tmux ``-A``) so the answer is the
        value in force at this scope rather than only one set on it.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    OptionResult
        Option name, its value, and the scope that was queried.
    """
    obj, opt_scope = _resolve_option_target(socket_name, scope, target)
    value = obj.show_option(
        option,
        global_=global_,
        scope=opt_scope,
        include_inherited=include_inherited or None,
    )
    return OptionResult(
        option=option,
        value=value,
        scope_queried=scope or ("global" if global_ else "server"),
        include_inherited=include_inherited,
    )


@handle_tool_errors
def set_option(
    option: str,
    value: str,
    scope: t.Literal["server", "session", "window", "pane"] | None = None,
    target: str | None = None,
    global_: bool = False,
    socket_name: str | None = None,
) -> OptionSetResult:
    """Set a tmux option value.

    Use to change tmux behavior at runtime. Common uses: adjusting
    history-limit, enabling mouse support, changing status bar format.

    Parameters
    ----------
    option : str
        The tmux option name to set.
    value : str
        The value to set.
    scope : str, optional
        Option scope.
    target : str, optional
        Target identifier. For session scope: session name
        (e.g. 'mysession'). For window scope: window ID (e.g. '@1').
        For pane scope: pane ID (e.g. '%1'). Requires scope.
    global_ : bool
        Whether to set the global option.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    OptionSetResult
        Confirmation with option name, value, and status.
    """
    obj, opt_scope = _resolve_option_target(socket_name, scope, target)
    obj.set_option(option, value, global_=global_, scope=opt_scope)
    return OptionSetResult(option=option, value=value, status="set")


def register(mcp: FastMCP) -> None:
    """Register option tools with the MCP instance."""
    mcp.tool(title="Show tmux Option", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        show_option
    )
    mcp.tool(
        title="Set tmux Option", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(set_option)
