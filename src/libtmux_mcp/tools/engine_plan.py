"""Chained plan tools from libtmux's experimental engine-ops adapter.

Opt-in (set ``LIBTMUX_MCP_ENGINE_OPS=1``). When enabled, this registers the
plan tier of libtmux's experimental MCP adapter onto the existing server:
``preview_plan`` / ``explain_plan`` / ``result_schema`` (readonly) and
``execute_plan`` (mutating). An agent chains multiple tmux operations that
*fold* into a few ``tmux a ; b`` dispatches, learning a step's result schema and
resolving forward-reference ids through the returned ``bindings`` map before
composing the next step.

The plan tools drive a subprocess engine bound to the target tmux server
(``LIBTMUX_SOCKET`` or the default socket), so no persistent connection is held.
The op-tier tools they reference are registered hidden behind the ``per-op`` tag.
The engine-ops adapter is unreleased (``libtmux.experimental``); this module is
usable only with the branch pin in ``pyproject.toml``.
"""

from __future__ import annotations

import os
import typing as t

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

#: Environment flag that opts into the engine-ops tiers (default off).
ENGINE_OPS_ENV = "LIBTMUX_MCP_ENGINE_OPS"

_TRUE = frozenset({"1", "true", "yes", "on"})


def enabled() -> bool:
    """Return whether the engine-ops tiers are opted in via the environment.

    Examples
    --------
    >>> import os
    >>> _prev = os.environ.pop(ENGINE_OPS_ENV, None)
    >>> enabled()
    False
    >>> os.environ[ENGINE_OPS_ENV] = "1"
    >>> enabled()
    True
    >>> _ = os.environ.pop(ENGINE_OPS_ENV, None)
    >>> os.environ.update({ENGINE_OPS_ENV: _prev} if _prev is not None else {})
    """
    return os.environ.get(ENGINE_OPS_ENV, "").strip().lower() in _TRUE


def register(mcp: FastMCP) -> None:
    """Register the chained plan tools onto *mcp* when opted in (else a no-op)."""
    if not enabled():
        return
    from libtmux.experimental.engines import AsyncSubprocessEngine
    from libtmux.experimental.mcp.fastmcp_adapter import register_plan_tools
    from libtmux.experimental.mcp.registry import OperationToolRegistry

    from libtmux_mcp._utils import _get_server

    engine = AsyncSubprocessEngine.for_server(_get_server())
    # The plan tools read the registry for result_schema and deserialize ops via
    # LazyPlan.from_list for execute_plan -- they need the registry, not the
    # per-op tool surface, so the op_* tools are intentionally not registered
    # (they would be un-hidden by libtmux-mcp's safety-tag gate and clutter it).
    register_plan_tools(mcp, engine, is_async=True, registry=OperationToolRegistry())
