"""MCP tools for tmux option management."""

from __future__ import annotations

import typing as t

from libtmux import exc
from libtmux.constants import OptionScope

from libtmux_mcp._tmux_format import escape_format
from libtmux_mcp._utils import (
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    TAG_MUTATING,
    TAG_READONLY,
    ExpectedToolError,
    _get_server,
    _raise_if_flag_like,
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
        valid = ", ".join(sorted(_SCOPE_MAP))
        msg = (
            f"scope is required when target is specified (target={target!r}). "
            f"Valid: {valid}"
        )
        raise ExpectedToolError(msg)

    if target is not None and opt_scope is not None:
        if opt_scope == OptionScope.Session:
            return _resolve_session(server, session_name=target), opt_scope
        if opt_scope == OptionScope.Window:
            return _resolve_window(server, window_id=target), opt_scope
        if opt_scope == OptionScope.Pane:
            return _resolve_pane(server, pane_id=target), opt_scope
    # An omitted target used to return the SERVER object, so the command
    # went out with no -t and tmux applied its own rule -- picking by
    # activity_time, which moves whenever a pane produces output. Every
    # other read tool resolved in Python. Two rules for "no target" in
    # one server, selected by whether the caller happened to name one.
    # Resolving the default here means no read path ever emits a tmux
    # command with an omitted target, so tmux's rule cannot run.
    if opt_scope in (None, OptionScope.Session):
        return _resolve_session(server), opt_scope
    if opt_scope == OptionScope.Window:
        return _resolve_window(server), opt_scope
    if opt_scope == OptionScope.Pane:
        return _resolve_pane(server), opt_scope
    return server, opt_scope


def _current_session_name(obj: t.Any) -> str | None:
    """Session tmux would treat as current, or None if it will not say.

    An untargeted option query resolves through tmux's current session,
    which tmux picks from attached clients. An MCP client has none, so
    the caller has no intuition for which session answered -- and the
    answer is stable and real, not arbitrary.
    """
    try:
        result = obj.cmd("display-message", "-p", "#{session_name}")
    except Exception:  # noqa: BLE001 - a disclosure, never a failure
        return None
    return result.stdout[0] if result.stdout else None


def _resolved_option_name(obj: t.Any, option: str, *flags: str) -> str | None:
    """Full option name tmux resolved ``option`` to, or None.

    tmux accepts an unambiguous prefix, so ``history-lim`` sets
    ``history-limit`` -- and echoing the caller's spelling back leaves
    them unable to confirm which option changed.
    """
    try:
        result = obj.cmd("show-options", *flags, option)
    except Exception:  # noqa: BLE001 - a disclosure, never a failure
        return None
    if not result.stdout:
        return None
    return result.stdout[0].split(" ", 1)[0] or None


def _raise_if_format_like(option: str) -> None:
    """Refuse an option name tmux's format expander would rewrite.

    ``set-option`` and ``show-options`` expand their NAME argument --
    ``@a#{pane_id}`` addresses ``@a%0``, and which pane that is depends
    on what the call resolved against, so the same name reaches
    different options over time.

    Escaping it is the obvious fix and it does not work: libtmux keys
    its ``show-options`` result by the name the caller asked for, while
    tmux answers under the name it stored, so an escaped name always
    reads back as "not set". The option would be writable and
    permanently unreadable. Refusing says so instead of pretending.

    Only the NAME is affected. Values are safe -- tmux gates that
    expansion behind ``-F``, which this server never passes.
    """
    if escape_format(option) != option:
        msg = (
            f"Option name {option!r} contains a tmux format sequence. tmux "
            "expands option names, so this would address a different option "
            "than the one named -- and the value could not be read back. "
            "Use a name without '#', or run_command for a raw tmux call."
        )
        raise ExpectedToolError(msg)


def _raise_unless_unset_user_option(option: str, err: exc.LibTmuxException) -> None:
    """Swallow tmux's error for a user option that is simply not set.

    The same call answers differently across the supported range. On
    tmux 3.2a -- the floor ``docs/installation.md`` declares -- reading
    an unset ``@option`` exits 0 with no value; from 3.3a onward it
    exits 1 with ``invalid option``. There is no version branching in
    this server, so a caller writing "read it, treat absent as unset"
    worked on the floor and raised on every other supported version.

    Normalised toward the floor, because "not set" is an ordinary
    answer to "what is this option" and an exception is a poor way to
    say it.

    Narrow on purpose. An unset user option and a MISTYPED built-in
    produce the identical message -- measured, ``@unset_probe`` and
    ``notarealoption`` both give ``invalid option: <name>`` -- so the
    only thing separating them is tmux's own rule that user options
    begin with ``@``. A typo in a built-in name still raises, which is
    the failure a caller needs to see.
    """
    if option.startswith("@") and "invalid option" in str(err).lower():
        return
    raise err


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
    _raise_if_format_like(option)
    obj, opt_scope = _resolve_option_target(socket_name, scope, target)
    try:
        value = obj.show_option(
            option,
            global_=global_,
            scope=opt_scope,
            include_inherited=include_inherited or None,
        )
    except exc.LibTmuxException as err:
        _raise_unless_unset_user_option(option, err)
        value = None
    return OptionResult(
        option=option,
        value=value,
        resolved_target=target or _current_session_name(obj),
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
    _raise_if_flag_like("Option name", option)
    _raise_if_format_like(option)
    obj.set_option(option, value, global_=global_, scope=opt_scope)
    return OptionSetResult(
        option=option,
        resolved_option=_resolved_option_name(
            obj, option, *(["-g"] if global_ else [])
        ),
        value=value,
        status="set",
    )


def register(mcp: FastMCP) -> None:
    """Register option tools with the MCP instance."""
    mcp.tool(title="Show tmux Option", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        show_option
    )
    mcp.tool(
        title="Set tmux Option", annotations=ANNOTATIONS_MUTATING, tags={TAG_MUTATING}
    )(set_option)
