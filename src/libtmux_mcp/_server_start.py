"""Explicit tmux server-start policy helpers."""

from __future__ import annotations

import threading
import typing as t

from libtmux import Server, exc
from libtmux.common import tmux_cmd

from libtmux_mcp._utils import ExpectedToolError, _server_not_running_error

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from libtmux.session import Session


_SERVER_START_DEFAULT_TOOLS = ("create_session",)
_SESSION_CREATION_LOCKS: dict[
    tuple[str | None, str | None, str | None], threading.Lock
] = {}
_SESSION_CREATION_LOCKS_GUARD = threading.Lock()


class NoStartServer(Server):
    """Server variant that forbids ``new-session`` from starting tmux."""

    def cmd(
        self,
        cmd: str,
        *args: t.Any,
        target: str | int | None = None,
    ) -> tmux_cmd:
        """Execute commands with tmux's no-start guard on session creation."""
        if cmd == "new-session":
            return super().cmd("-N", cmd, *args, target=target)
        return super().cmd(cmd, *args, target=target)


def _resolve_allow_server_start(value: str | None) -> bool:
    """Resolve the strict startup daemon-creation setting."""
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    msg = "LIBTMUX_ALLOW_SERVER_START must be unset, '0', or '1'"
    raise ValueError(msg)


def _validate_allow_server_start(value: object) -> bool:
    """Require exact direct-call startup permission semantics."""
    if type(value) is not bool:
        msg = "allow_server_start must be a bool"
        raise ExpectedToolError(msg)
    return value


def _creation_lock(server: Server) -> threading.Lock:
    """Return the process-local creation lock for an effective target."""
    key = (
        server.socket_name,
        str(server.socket_path) if server.socket_path is not None else None,
        str(server.tmux_bin) if server.tmux_bin is not None else None,
    )
    with _SESSION_CREATION_LOCKS_GUARD:
        return _SESSION_CREATION_LOCKS.setdefault(key, threading.Lock())


def _clone_no_start_server(server: Server) -> NoStartServer:
    """Clone the command-affecting settings of an effective server."""
    return NoStartServer(
        socket_name=server.socket_name,
        socket_path=server.socket_path,
        config_file=server.config_file,
        colors=server.colors,
        tmux_bin=server.tmux_bin,
    )


def _is_daemon_not_up_error(error: BaseException) -> bool:
    """Match only tmux's no-start failure for an absent target."""
    if not isinstance(error, exc.LibTmuxException):
        return False
    message = str(error)
    if not message.startswith("new-session: "):
        return False
    return "no server running on " in message or (
        "error connecting to " in message and "(No such file or directory)" in message
    )


def _create_session_with_start_policy(
    server: Server,
    *,
    allow_server_start: bool,
    session_kwargs: dict[str, t.Any],
) -> tuple[Session, bool]:
    """Create a session while enforcing startup permission at execution."""
    with _creation_lock(server):
        no_start_server = _clone_no_start_server(server)
        try:
            return no_start_server.new_session(**session_kwargs), False
        except exc.LibTmuxException as error:
            if not _is_daemon_not_up_error(error):
                raise
        if not allow_server_start:
            raise _server_not_running_error()
        return server.new_session(**session_kwargs), True


def _configure_server_start_default(
    mcp: FastMCP,
    enabled: bool,
    *,
    tool_names: tuple[str, ...] = _SERVER_START_DEFAULT_TOOLS,
) -> None:
    """Publish the effective MCP default for daemon creation.

    Parameters
    ----------
    mcp : FastMCP
        Server receiving the public tool transform.
    enabled : bool
        Effective startup default to publish.
    tool_names : tuple[str, ...]
        Tools that inherit the default when omitted by an MCP caller.
    """
    from fastmcp.server.transforms import ToolTransform
    from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig

    argument = ArgTransformConfig(default=enabled)
    mcp.add_transform(
        ToolTransform(
            {
                name: ToolTransformConfig(arguments={"allow_server_start": argument})
                for name in tool_names
            }
        )
    )
