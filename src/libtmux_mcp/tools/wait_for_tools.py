"""MCP tools for tmux ``wait-for`` channel synchronisation.

``tmux wait-for`` exposes named, server-global channels that clients can
signal and block on. These give agents an explicit synchronisation
primitive that's strictly cheaper than polling pane content: instead of
scraping ``capture_pane`` at 50 ms ticks waiting for a sentinel line,
the agent composes the shell command with ``tmux wait-for -S NAME`` and
then calls :func:`wait_for_channel` which blocks server-side until the
signal fires.

Wait channel safety
-------------------
``tmux wait-for`` without a timeout blocks indefinitely at the OS level.
If the shell command that was supposed to emit the signal crashes
before it ran, the wait would deadlock the MCP server and every agent
connected to it. :func:`wait_for_channel` therefore *requires* a
timeout and runs tmux through
:func:`~libtmux_mcp._tmux_proc._run_tmux_bounded`, which kills the
child on expiry *and* on cancellation. That bound is itself capped by
the same server wait ceiling
:func:`~libtmux_mcp._wait_policy._wait_ceiling_seconds` publishes for
``wait_for_text`` — an over-large ``timeout`` is clamped, not honoured
verbatim. Agents SHOULD use the safe composition pattern::

    send_keys("pytest; tmux wait-for -S tests_done")

Shell ``;`` semantics fire ``wait-for -S`` whether ``pytest`` succeeded
or failed, so the edge-triggered signal never deadlocks the wait. Do
NOT chain ``exit $status`` after the signal — in interactive shells
that exits the shell itself, which destroys single-pane sessions and
takes the tmux server down with them. Exit-status preservation in
interactive shells is out-of-scope; inspect the captured output for
command-specific success markers.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import typing as t

from libtmux_mcp._tmux_proc import _run_tmux_bounded
from libtmux_mcp._utils import (
    ANNOTATIONS_MUTATING,
    TAG_MUTATING,
    TAG_SELF_BOUNDED,
    ExpectedToolError,
    _get_server_async,
    _tmux_argv,
    handle_tool_errors_async,
)
from libtmux_mcp._wait_policy import _wait_ceiling_seconds

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

#: Cap on the post-wait liveness re-probe. Same reasoning as
#: ``_SIGNAL_TIMEOUT_SECONDS``, and it runs at most once per wait.
_LIVENESS_TIMEOUT_SECONDS = 5.0


async def _server_is_alive(server: Server) -> bool:
    """Return whether the tmux server still answers on its socket.

    ``list-sessions`` is the probe libtmux's own ``Server.is_alive`` uses.
    It is safe here specifically because it does NOT auto-start a server:
    against a socket with no server it exits non-zero with
    ``error connecting to <path>``, so probing cannot resurrect the thing
    it is asking about.

    A ``False`` return is deliberately treated as fatal by the caller
    rather than merely logged. There is a narrow race — a script that
    signals and then immediately tears the server down would report an
    error for a wait that genuinely succeeded — but that error names a
    true fact about the server, whereas the alternative is telling the
    agent a channel was signalled when nothing signalled it.
    """
    argv = _tmux_argv(server, "list-sessions")
    try:
        returncode, _stdout, _stderr = await _run_tmux_bounded(
            argv, timeout=_LIVENESS_TIMEOUT_SECONDS
        )
    except (TimeoutError, OSError):
        return False
    return returncode == 0


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
    ExpectedToolError
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
    libtmux_mcp._utils.ExpectedToolError: Invalid channel name: 'has space'
    >>> _validate_channel_name("")
    Traceback (most recent call last):
    ...
    libtmux_mcp._utils.ExpectedToolError: Invalid channel name: ''
    """
    if not _CHANNEL_NAME_RE.fullmatch(name):
        msg = f"Invalid channel name: {name!r}"
        raise ExpectedToolError(msg)
    return name


@handle_tool_errors_async
async def wait_for_channel(
    channel: str,
    timeout: float = 30.0,
    socket_name: str | None = None,
) -> str:
    """Block until a tmux ``wait-for`` channel is signalled.

    This is the AUTHORED-output synchronisation primitive: the channel
    only fires because your own composed shell command signals it.
    Reserve ``wait_for_text`` for output you did not author.

    Agents can compose this with ``send_keys`` to turn shell-side
    milestones into explicit synchronisation points::

        send_keys(
            "pytest; tmux wait-for -S tests_done",
            pane_id=...,
        )
        wait_for_channel("tests_done", timeout=60)

    Shell ``;`` semantics fire ``wait-for -S`` whether the command
    succeeded or failed, so the edge-triggered signal never deadlocks
    on a crash. Do NOT chain ``exit $status`` after the signal — in an
    interactive shell that exits the shell itself, which destroys
    single-pane sessions. Exit-status preservation in interactive
    shells is out-of-scope; inspect the captured output for
    command-specific success markers.

    Parameters
    ----------
    channel : str
        Channel name. Must match ``^[A-Za-z0-9_.:-]{1,128}$``.
    timeout : float
        Maximum seconds to wait. The underlying ``tmux wait-for`` has
        no built-in timeout — this wrapper enforces it by killing the
        tmux child, which also happens if the call is cancelled.
        Defaults to 30 seconds. Capped by the same server wait ceiling
        as ``wait_for_text``; an over-large value is not an error, the
        wait returns at the ceiling and the confirmation message names
        the timeout that was actually enforced.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message naming the channel and the timeout
        actually enforced.

    Raises
    ------
    ExpectedToolError
        On timeout, invalid channel name, tmux error, or when the tmux
        server disappeared during the wait — ``tmux wait-for`` exits 0
        for a clean server shutdown exactly as it does for a real
        signal, so that case is detected by re-probing the server and
        reported rather than passed off as success.
    """
    server = await _get_server_async(socket_name=socket_name)
    cname = _validate_channel_name(channel)
    effective_timeout = min(timeout, _wait_ceiling_seconds())
    argv = _tmux_argv(server, "wait-for", cname)
    # FastMCP direct-awaits async tools on its event loop, and ``tmux
    # wait-for`` blocks for the full timeout, so the child must not run
    # on the loop. It must not run on a worker thread either:
    # ``asyncio.to_thread(subprocess.run, ...)`` cannot be interrupted,
    # so a cancelled call returned instantly while the tmux child stayed
    # blocked for the rest of its budget — measured at 13 s of orphan
    # for a 15 s wait cancelled at 2 s. ``_run_tmux_bounded`` owns a
    # killable async subprocess instead, the same one
    # :func:`~libtmux_mcp.tools.pane_tools.wait.wait_for_text` uses.
    try:
        returncode, _stdout, stderr = await _run_tmux_bounded(
            argv, timeout=effective_timeout
        )
    except TimeoutError as e:
        msg = (
            f"wait-for timeout: channel {cname!r} was not signalled within "
            f"{effective_timeout}s"
        )
        raise ExpectedToolError(msg) from e
    if returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        msg = f"wait-for failed for channel {cname!r}: {detail or f'exit {returncode}'}"
        raise ExpectedToolError(msg)
    # A zero exit does NOT mean "signalled". ``tmux wait-for`` also exits
    # 0 when the server goes away without ever signalling the channel,
    # and it is silent about it. Measured on tmux 3.7b, all three of
    # these are rc=0 with empty stderr and therefore indistinguishable
    # from each other by exit status alone:
    #
    # ==================  ====  ==================================
    # how the wait ended  rc    stderr
    # ==================  ====  ==================================
    # genuinely signalled  0    *(empty)*
    # ``kill-server``      0    *(empty)*
    # server SIGTERM       0    *(empty)*
    # server SIGKILL       1    ``server exited unexpectedly``
    # ==================  ====  ==================================
    #
    # Only the SIGKILL path was already caught. Re-probe liveness so the
    # other two stop being reported as success: this tool exists to be
    # the deterministic primitive the fuzzy ones defer to, and a silent
    # false "was signalled" is the worst answer it can give.
    if not await _server_is_alive(server):
        msg = (
            f"wait-for returned for channel {cname!r} but the tmux server is "
            "no longer running, so the channel was probably never signalled "
            "— tmux exits 0 for both. Re-check the work you were waiting on."
        )
        raise ExpectedToolError(msg)
    return f"Channel {cname!r} was signalled (timeout {effective_timeout}s)"


@handle_tool_errors_async
async def signal_channel(
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
    server = await _get_server_async(socket_name=socket_name)
    cname = _validate_channel_name(channel)
    argv = _tmux_argv(server, "wait-for", "-S", cname)
    # Deliberately still a worker thread, unlike every other tmux call
    # in this package. The orphan-on-cancel defect that pushed the
    # waits onto ``_run_tmux_bounded`` needs a child that blocks for a
    # caller-chosen duration; ``wait-for -S`` is edge-triggered and
    # returns in milliseconds, so the worst case here is a 5 s child
    # against an already-wedged tmux — and that bound is ours, not the
    # caller's. Converting it would buy nothing and change this tool's
    # error messages.
    try:
        await asyncio.to_thread(
            subprocess.run,
            argv,
            check=True,
            capture_output=True,
            timeout=_SIGNAL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        msg = (
            f"signal-channel timeout after {_SIGNAL_TIMEOUT_SECONDS}s: "
            f"channel {cname!r}"
        )
        raise ExpectedToolError(msg) from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        msg = f"signal-channel failed for channel {cname!r}: {stderr or e}"
        raise ExpectedToolError(msg) from e
    return f"Channel {cname!r} signalled"


def register(mcp: FastMCP) -> None:
    """Register wait-for channel tools with the MCP instance."""
    # ``wait_for_channel``'s ``timeout`` is clamped to the shared wait
    # ceiling (see ``_wait_policy``), but a 1000-operation batch would
    # still multiply that ceiling by the operation count. ``TAG_SELF_BOUNDED``
    # keeps it out of the batch wrappers; the tool is already bounded
    # per-call by the killable tmux child in ``_run_tmux_bounded``,
    # which is what the tag asserts.
    mcp.tool(
        title="Wait For tmux Channel",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING, TAG_SELF_BOUNDED},
    )(wait_for_channel)
    mcp.tool(
        title="Signal tmux Channel",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(signal_channel)
