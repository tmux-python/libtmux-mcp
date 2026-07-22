"""Watcher tools from libtmux's experimental engine-ops event stream.

Opt-in via the same ``LIBTMUX_MCP_ENGINE_OPS=1`` flag as
:mod:`libtmux_mcp.tools.engine_plan`. When enabled this registers libtmux's
control-mode event/monitor tools onto the existing server:

- ``wait_for_output`` -- a needle-free settle monitor: it folds a pane's live
  ``%output`` and returns when the pane goes quiet after a command, reporting
  whether the pane's process exited. It replaces sleep-then-``capture_pane``
  polling.
- ``watch_events`` (push mode) and/or the ``tmux://events`` resource +
  ``poll_events`` cursor (pull mode) over the control-mode notification stream.

These require a persistent ``tmux -C`` control connection, so this module holds
one process-global :class:`~libtmux.experimental.engines.AsyncControlModeEngine`
bound to the target server; :func:`astartup` / :func:`ashutdown` open and close
it from the server lifespan. Streaming is non-blocking: the engine's single
reader fans out to bounded per-subscriber queues (drop-oldest), and the settle
monitor pulls frames with a bounded wait -- consumers never block the producer.

The engine-ops adapter is unreleased (``libtmux.experimental``); this module is
usable only with the branch pin in ``pyproject.toml``.
"""

from __future__ import annotations

import os
import typing as t

from libtmux_mcp.tools.engine_plan import ENGINE_OPS_ENV, enabled

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from libtmux.experimental.engines import AsyncControlModeEngine

__all__ = ["ENGINE_OPS_ENV", "ashutdown", "astartup", "enabled", "register"]

#: The single control-mode engine backing the watcher tools, or ``None`` when
#: the engine-ops tier is not opted in. Opened by :func:`astartup`, closed by
#: :func:`ashutdown`.
_engine: AsyncControlModeEngine | None = None


def register(mcp: FastMCP) -> None:
    """Register the watcher tools onto *mcp* when opted in (else a no-op).

    Builds one control-mode engine bound to the target server and registers the
    event/monitor tools; the engine is started and closed by :func:`astartup` /
    :func:`ashutdown` from the server lifespan.
    """
    global _engine
    if not enabled():
        return
    from libtmux.experimental.engines import AsyncControlModeEngine
    from libtmux.experimental.mcp.events import register_events

    from libtmux_mcp._utils import _get_server

    engine = AsyncControlModeEngine.for_server(_get_server())
    # os.environ.get returns str; register_events wants the EventMode/EventSource
    # literals, so narrow (an out-of-range value registers only the monitor).
    mode = t.cast("t.Any", os.environ.get("LIBTMUX_MCP_EVENTS", "push"))
    source = t.cast("t.Any", os.environ.get("LIBTMUX_MCP_EVENT_SOURCE", "subscription"))
    register_events(mcp, engine, mode=mode, source=source)
    _engine = engine


async def astartup() -> None:
    """Start the control-mode engine on the server loop (a no-op when off).

    A ``list-sessions`` probe that doubles as the engine's first use: it spawns
    ``tmux -C`` and launches the reader task, so ``watch_events`` / ``poll_events``
    subscribe to a live stream rather than a dead one.
    """
    if _engine is None:
        return
    from libtmux.experimental.engines.base import CommandRequest

    try:
        await _engine.run(CommandRequest.from_args("list-sessions"))
    except Exception as error:
        msg = f"tmux control-mode engine preflight failed: {error}"
        raise RuntimeError(msg) from error


async def ashutdown() -> None:
    """Close the control-mode engine on shutdown (a no-op when off)."""
    global _engine
    if _engine is None:
        return
    engine, _engine = _engine, None
    await engine.aclose()
