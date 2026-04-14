"""MCP tools for tmux ``wait-for`` channel synchronisation.

``tmux wait-for`` exposes named, server-global channels that clients can
signal and block on. These give agents an explicit synchronisation
primitive that's strictly cheaper than polling pane content: instead of
scraping ``capture_pane`` at 50 ms ticks waiting for a sentinel line,
the agent composes the shell command with ``tmux wait-for -S NAME`` and
then calls :func:`wait_for_channel` which blocks server-side until the
signal fires.

Safety
------
``tmux wait-for`` without a timeout blocks indefinitely at the OS level.
If the shell command that was supposed to emit the signal crashes
before it ran, the wait would deadlock the MCP server and every agent
connected to it. :func:`wait_for_channel` therefore *requires* a
timeout and wraps the underlying ``subprocess.run`` call in
``timeout=timeout``. Agents SHOULD use the safe composition pattern::

    send_keys("pytest; status=$?; tmux wait-for -S tests_done; exit $status")

This ensures the signal fires on both success and failure paths.
"""

from __future__ import annotations

import re
import subprocess
import typing as t

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    ANNOTATIONS_MUTATING,
    TAG_MUTATING,
    _get_server,
    handle_tool_errors,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from libtmux.server import Server

#: Allowed characters and length range for channel names. Channels are
#: tmux-server-global and names are passed to ``tmux wait-for`` on the
#: command line — defending against shell-surface escapes / oversized
#: inputs at the MCP boundary is cheaper than relying on libtmux's
#: argv handling.
_CHANNEL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

#: Cap on ``signal_channel`` subprocess. ``wait-for -S`` is a short
#: server-local operation; 5 s is a generous ceiling that still bounds
#: pathological hangs (e.g. tmux server deadlock).
_SIGNAL_TIMEOUT_SECONDS = 5.0


def _validate_channel_name(name: str) -> str:
    """Return ``name`` unchanged if it is a valid channel name.

    Parameters
    ----------
    name : str
        Candidate channel name.

    Returns
    -------
    str
        The same string, validated.

    Raises
    ------
    ToolError
        When ``name`` is empty, too long, or contains disallowed
        characters.

    Examples
    --------
    >>> _validate_channel_name("tests_done")
    'tests_done'
    >>> _validate_channel_name("deploy.prod")
    'deploy.prod'
    >>> _validate_channel_name("ns:ready-2")
    'ns:ready-2'
    >>> _validate_channel_name("has space")
    Traceback (most recent call last):
    ...
    fastmcp.exceptions.ToolError: Invalid channel name: 'has space'
    >>> _validate_channel_name("")
    Traceback (most recent call last):
    ...
    fastmcp.exceptions.ToolError: Invalid channel name: ''
    """
    if not _CHANNEL_NAME_RE.fullmatch(name):
        msg = f"Invalid channel name: {name!r}"
        raise ToolError(msg)
    return name


def _tmux_argv(server: Server, *tmux_args: str) -> list[str]:
    """Build a full tmux argv list honouring the server's socket."""
    tmux_bin: str = getattr(server, "tmux_bin", None) or "tmux"
    argv: list[str] = [tmux_bin]
    if server.socket_name:
        argv.extend(["-L", server.socket_name])
    if server.socket_path:
        argv.extend(["-S", str(server.socket_path)])
    argv.extend(tmux_args)
    return argv


@handle_tool_errors
def wait_for_channel(
    channel: str,
    timeout: float = 30.0,
    socket_name: str | None = None,
) -> str:
    """Block until a tmux ``wait-for`` channel is signalled.

    Agents can compose this with ``send_keys`` to turn shell-side
    milestones into explicit synchronisation points::

        send_keys(
            "pytest; status=$?; tmux wait-for -S tests_done; exit $status",
            pane_id=...,
        )
        wait_for_channel("tests_done", timeout=60)

    The ``status=$?; ...; exit $status`` idiom is important: ``wait-for``
    is edge-triggered, so if the shell command crashes before issuing
    the signal the wait will block until ``timeout``. Emitting the
    signal unconditionally (success or failure) avoids that penalty.

    Parameters
    ----------
    channel : str
        Channel name. Must match ``^[A-Za-z0-9_.:-]{1,128}$``.
    timeout : float
        Maximum seconds to wait. The underlying ``tmux wait-for`` has
        no built-in timeout — this wrapper enforces it via
        ``subprocess.run(timeout=...)``. Defaults to 30 seconds.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message naming the channel.

    Raises
    ------
    ToolError
        On timeout, invalid channel name, or tmux error.
    """
    server = _get_server(socket_name=socket_name)
    cname = _validate_channel_name(channel)
    argv = _tmux_argv(server, "wait-for", cname)
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        msg = f"wait-for timeout: channel {cname!r} was not signalled within {timeout}s"
        raise ToolError(msg) from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        msg = f"wait-for failed for channel {cname!r}: {stderr or e}"
        raise ToolError(msg) from e
    return f"Channel {cname!r} was signalled"


@handle_tool_errors
def signal_channel(
    channel: str,
    socket_name: str | None = None,
) -> str:
    """Signal a tmux ``wait-for`` channel, waking any blocked waiters.

    Signalling an unwaited channel is a no-op that still returns
    successfully — safe to call defensively.

    Parameters
    ----------
    channel : str
        Channel name. Must match ``^[A-Za-z0-9_.:-]{1,128}$``.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message naming the channel.
    """
    server = _get_server(socket_name=socket_name)
    cname = _validate_channel_name(channel)
    argv = _tmux_argv(server, "wait-for", "-S", cname)
    try:
        subprocess.run(
            argv, check=True, capture_output=True, timeout=_SIGNAL_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as e:
        msg = (
            f"signal-channel timeout after {_SIGNAL_TIMEOUT_SECONDS}s: "
            f"channel {cname!r}"
        )
        raise ToolError(msg) from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        msg = f"signal-channel failed for channel {cname!r}: {stderr or e}"
        raise ToolError(msg) from e
    return f"Channel {cname!r} signalled"


def register(mcp: FastMCP) -> None:
    """Register wait-for channel tools with the MCP instance."""
    mcp.tool(
        title="Wait For Channel",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(wait_for_channel)
    mcp.tool(
        title="Signal Channel",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(signal_channel)
