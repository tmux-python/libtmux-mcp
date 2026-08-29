"""Read-only MCP tools for tmux hook introspection.

Why read-only only
------------------
Write-hooks (``set-hook`` / ``unset-hook``) are deliberately excluded.
The reason is side-effect leakage: tmux servers outlive the MCP
process, so if an MCP agent installs a hook that runs arbitrary shell
on ``pane-exited`` or ``command-error`` and then the MCP server is
``kill -9``'d, OOM'd, or crashes via a C-extension fault, the hook
**stays installed** in the user's persistent tmux server and fires
forever.

FastMCP ``lifespan`` teardown only runs on graceful SIGTERM/SIGINT, so
a soft "track what we installed and unset on shutdown" registry cannot
close this gap. Three plausible future paths are open:

* Install a tmux-side meta-hook on ``client-detached`` that self-cleans
  all ``libtmux_mcp_*``-namespaced hooks when the MCP client disconnects.
  Survives hard crashes because tmux enforces it.
* Require ``LIBTMUX_SAFETY=destructive`` for write-hooks so leakage is
  an explicit opt-in with user awareness.
* Expose ``run_hook`` (one-shot fire) but not ``set_hook`` (persistent
  install) — narrows the risk surface to transient events.

Until one is implemented, the surface here is deliberately visibility
only.
"""

from __future__ import annotations

import typing as t

from libtmux import exc as libtmux_exc
from libtmux.constants import OptionScope
from libtmux.pane import Pane
from libtmux.session import Session
from libtmux.window import Window

from libtmux_mcp._utils import (
    ANNOTATIONS_RO,
    TAG_READONLY,
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    _resolve_session,
    _resolve_window,
    handle_tool_errors,
)
from libtmux_mcp.models import HookEntry, HookListResult

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from libtmux.hooks import HooksMixin
    from libtmux.options import OptionsMixin


_SCOPE_MAP: dict[str, OptionScope] = {
    "server": OptionScope.Server,
    "session": OptionScope.Session,
    "window": OptionScope.Window,
    "pane": OptionScope.Pane,
}


def _resolve_hook_target(
    socket_name: str | None,
    scope: t.Literal["server", "session", "window", "pane"] | None,
    target: str | None,
) -> tuple[HooksMixin, OptionScope | None]:
    """Resolve the target object and scope for hook queries.

    Mirrors the pattern used by :mod:`libtmux_mcp.tools.option_tools`,
    but returns ``scope=None`` when the resolved object already carries
    that scope implicitly. tmux's ``show-hooks`` command builds
    different argv depending on whether the scope flag is set, and
    passing a redundant explicit scope to a Session/Window/Pane object
    triggers ``"too many arguments"`` on some tmux builds.

    TODO(libtmux upstream): ``Session.show_hook(scope=OptionScope.Session)``
    mis-builds the CLI argv and produces ``"too many arguments"`` on
    current tmux builds. Resetting ``scope`` to ``None`` after we've
    resolved to a concrete object makes libtmux use the object's
    default, which sidesteps the mis-built argv. File upstream once
    reduced to a minimal repro — the fix belongs in libtmux's
    ``HooksMixin._show_hook`` argv-assembly path.
    """
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
        # Session only: its object default IS the session tree, so the
        # scope is redundant there and passing it mis-builds the argv into
        # an empty listing. Window and Pane also default to the SESSION
        # tree, so dropping the scope for them answers about a different
        # object -- show_hooks(scope="window", target=@0) returns the
        # session's hooks. They take the explicit scope without the argv
        # problem.
        if opt_scope == OptionScope.Session:
            return _resolve_session(server, session_name=target), None
        if opt_scope == OptionScope.Window:
            return _resolve_window(server, window_id=target), opt_scope
        if opt_scope == OptionScope.Pane:
            return _resolve_pane(server, pane_id=target), opt_scope
    # Same seam the option tools had: an omitted target returned the
    # SERVER object, the command went out with no -t, and tmux resolved
    # it by activity_time while every other read tool resolved in
    # Python. Server scope genuinely has no target and keeps the server.
    if opt_scope in (None, OptionScope.Session):
        return _resolve_session(server), None
    if opt_scope == OptionScope.Window:
        return _resolve_window(server), opt_scope
    if opt_scope == OptionScope.Pane:
        return _resolve_pane(server), opt_scope
    return server, opt_scope


def _target_label(obj: t.Any) -> str | None:
    """Tmux id the query was answered for, or None for the server.

    Dispatched on TYPE, not on which attribute happens to exist: a
    libtmux ``Session`` carries ``window_id`` and ``pane_id`` too, so
    taking the first attribute present reported ``%0`` for a
    session-scope query.
    """
    if isinstance(obj, Pane):
        return obj.pane_id
    if isinstance(obj, Window):
        return obj.window_id
    if isinstance(obj, Session):
        return obj.session_id
    return None


def _split_indexed_hook_name(key: str) -> tuple[str, int | None]:
    """Parse ``pane-focus-in[0]`` → ``('pane-focus-in', 0)``.

    ``show_hooks`` (plural, enumerating path) returns keys with the
    tmux-native ``NAME[N]`` array suffix baked into the dict key, while
    ``show_hook`` (singular, name-targeted path) returns a nested
    ``{int: str}`` mapping with a clean name. Splitting the indexed
    form at the MCP serialization layer normalizes both paths into the
    same ``HookEntry`` shape so agents don't have to distinguish them.
    """
    if key.endswith("]") and "[" in key:
        base, bracket = key.rsplit("[", 1)
        try:
            return base, int(bracket[:-1])
        except ValueError:
            return key, None
    return key, None


def _flatten_hook_value(
    hook_name: str,
    value: t.Any,
) -> list[HookEntry]:
    """Turn a tmux ``show_hook``/``show_hooks`` value into entries.

    tmux hook values come in four shapes:

    * ``None`` — hook is unset.
    * scalar string / int — single command with no array index.
    * ``dict[int, str]`` — array hook returned by ``show_hook(name)``.
    * ``SparseArray`` — array hook returned by some paths.

    Both array shapes implement ``.items()`` yielding ``(int, str)``,
    so a single ``hasattr`` check handles them uniformly. Scalars
    flatten into a single ``HookEntry`` with ``index=None``. An empty
    list means "hook is unset".

    The ``hook_name`` may arrive in the ``NAME[N]`` form from the
    plural enumeration path; it's split into clean name + index here
    to match the shape the singular name-lookup path emits.
    """
    if value is None:
        return []
    name, suffix_index = _split_indexed_hook_name(hook_name)
    if hasattr(value, "items"):
        # SparseArray or dict[int, str] — both yield (int, str) pairs.
        return [
            HookEntry(hook_name=name, index=int(idx), command=str(cmd))
            for idx, cmd in value.items()
        ]
    return [HookEntry(hook_name=name, index=suffix_index, command=str(value))]


@handle_tool_errors
def show_hooks(
    scope: t.Literal["server", "session", "window", "pane"] | None = None,
    target: str | None = None,
    global_: bool = False,
    socket_name: str | None = None,
) -> HookListResult:
    """List configured tmux hooks at the given scope.

    Enumerates the hooks SET at the requested scope. A name-targeted
    :func:`show_hook` resolves with tmux's inheritance instead, so it
    can answer with a session hook when asked at window or pane scope
    while this returns nothing there. Both are tmux's own semantics --
    the difference is enumerate-versus-resolve, not a disagreement
    about what is in force.

    ``scope="server"`` enumerates hooks installed via
    ``tmux set-hook -g ...``. tmux splits those globals across two
    options trees by hook category: session-level hooks
    (``session-closed``, ``client-*``, etc.) live in the
    global-session tree enumerated by ``show-hooks -g``, while
    pane/window-level hooks (``pane-focus-in``, ``window-resized``,
    etc.) live in the global-window tree enumerated by
    ``show-hooks -gw``. This tool consults both trees and merges the
    results so the enumeration matches what a name-targeted
    :func:`~libtmux_mcp.tools.hook_tools.show_hook` call would return.

    Parameters
    ----------
    scope : str, optional
        Hook scope (server/session/window/pane). Defaults to the
        calling object's scope when a ``target`` is given.
    target : str, optional
        Target identifier. For session scope: session name. For window
        scope: window ID. For pane scope: pane ID. Requires ``scope``.
    global_ : bool
        Pass ``-g`` to query global hooks. Default False.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    HookListResult
        Flat list of hook-name / index / command entries.
    """
    obj, opt_scope = _resolve_hook_target(socket_name, scope, target)
    raw: dict[str, t.Any] = obj.show_hooks(global_=global_, scope=opt_scope)

    if target is None and scope in (None, "server"):
        # tmux does not unify a listing across the session and window
        # trees, so one query misses half of what a name-targeted
        # ``show_hook`` finds.
        #
        # WHICH window tree depends on the base query, and the two are not
        # interchangeable: scope="server" queries the global session tree,
        # so the global window tree is its counterpart; scope=None queries
        # THIS session's, so the counterpart is this window's. An explicit
        # global_=True makes the base global whatever the scope says, so it
        # must be carried too, or the CURRENT window's hooks land on the
        # globals.
        raw_window = obj.show_hooks(
            global_=global_ or scope == "server", scope=OptionScope.Window
        )
        for name, value in raw_window.items():
            raw.setdefault(name, value)

    entries: list[HookEntry] = []
    for name, value in sorted(raw.items()):
        entries.extend(_flatten_hook_value(name, value))
    return HookListResult(entries=entries, resolved_target=_target_label(obj))


@handle_tool_errors
def show_hook(
    hook_name: str,
    scope: t.Literal["server", "session", "window", "pane"] | None = None,
    target: str | None = None,
    global_: bool = False,
    include_inherited: bool = False,
    socket_name: str | None = None,
) -> HookListResult:
    """Look up a specific tmux hook by name.

    Returns a :class:`~libtmux_mcp.models.HookListResult` with zero or
    more :class:`~libtmux_mcp.models.HookEntry` rows — one if the hook
    is a scalar, several if it is an array hook with sparse indices.

    .. warning::
       ``entries: []`` means "not set AT THIS SCOPE", not "not set". A
       hook set with ``set-hook -g`` is in force and WILL fire, and this
       still answers zero for it, because tmux's ``show-hooks <name>``
       does not consult wider scopes. Pass ``include_inherited=True``
       (tmux's ``-A``) for the value actually in force, exactly as
       :func:`~libtmux_mcp.tools.option_tools.show_option` does — the
       two are the same question about the same underlying store, since
       tmux keeps hooks in the options table.

    Parameters
    ----------
    hook_name : str
        Hook to look up (e.g. ``"pane-exited"``).
    include_inherited : bool
        Resolve inherited values (tmux ``-A``) so the answer is the hook
        in force at this scope rather than only one set on it.
    scope, target, global_, socket_name : see
        :func:`~libtmux_mcp.tools.hook_tools.show_hooks`.

    Returns
    -------
    HookListResult
        One or more :class:`~libtmux_mcp.models.HookEntry` rows, or empty
        if the hook is unset at the scope queried.
    """
    obj, opt_scope = _resolve_hook_target(socket_name, scope, target)
    if include_inherited:
        # tmux stores hooks in the options table and only the OPTIONS
        # lookup honours -A: with `set-hook -g alert-bell`, `show-hooks -t
        # A alert-bell` is empty while `show-options -A -t A alert-bell`
        # returns it, flagged `*`, which libtmux strips.
        return HookListResult(
            entries=_flatten_hook_value(
                hook_name,
                # Every concrete target -- Server, Session, Window,
                # Pane -- carries both mixins; only libtmux's HooksMixin
                # does not declare OptionsMixin as a base.
                t.cast("OptionsMixin", obj).show_option(
                    hook_name,
                    global_=global_,
                    scope=opt_scope,
                    include_inherited=True,
                ),
            ),
            resolved_target=_target_label(obj),
            include_inherited=True,
        )
    try:
        value = obj.show_hook(hook_name, global_=global_, scope=opt_scope)
    except libtmux_exc.OptionError as e:
        # tmux rejects ``show-hooks <name>`` for an *unset* hook with "too
        # many arguments" on every supported build, and that message alone
        # reads as the empty result. A genuine name error must surface, or
        # an agent reads its own typo as "hook is unset".
        if "too many arguments" in str(e):
            return HookListResult(entries=[], resolved_target=_target_label(obj))
        raise
    return HookListResult(
        entries=_flatten_hook_value(hook_name, value),
        resolved_target=_target_label(obj),
    )


def register(mcp: FastMCP) -> None:
    """Register read-only hook tools with the MCP instance."""
    mcp.tool(title="Show tmux Hooks", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        show_hooks
    )
    mcp.tool(title="Show tmux Hook", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        show_hook
    )
