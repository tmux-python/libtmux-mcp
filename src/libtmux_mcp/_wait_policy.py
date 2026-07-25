"""Duration policy for the pane wait tools.

A wait tool is the only MCP tool in this server that blocks for a
caller-chosen duration. The ceiling bounds the AGENT'S TURN, not the
transport: the wait tools await throughout, so a long wait does not
stall the connection. Measured on fastmcp 3.4.4 over stdio, a tool
awaiting for 6 s served 58 interleaved calls at a 3.4 ms median, while
a control tool that blocked the event loop for the same 6 s served
exactly one, at 6014 ms. What an unbounded wait costs is the agent:
it picks the wrong search text once and the whole turn is gone with
nothing to show for it, and MCP gives the agent no way to change its
mind mid-call. The ceiling makes that failure cheap and repeatable.

The second reason is specific to how ``wait_for_text`` works. Its
baseline is an absolute grid anchor, and ``grid_collect_history``
(``grid.c``) frees the oldest scrollback once ``history-limit`` is
reached, which destroys that anchor. The longer a wait runs on a
productive pane, the likelier it is to be observing through an anchor
tmux has already invalidated. Bounding the wait bounds that exposure.

Do not "fix" the connection with background tasks. There is nothing to
unblock — see :doc:`/topics/waiting` for the measurement.

This module owns the ceiling: it resolves the operator-facing
``LIBTMUX_MCP_WAIT_MAX_SECONDS`` environment variable and publishes the
result to the tool modules. Kept out of ``libtmux_mcp.server`` on
purpose — the tool modules must not import server globals, so
``server`` resolves the value at import and calls
:func:`_configure_wait_ceiling` at tool-registration time, mirroring
:func:`libtmux_mcp._history._configure_history_defaults`.

Clamp, never reject. An over-large ``timeout`` argument is not an
error: the tool honours the ceiling instead and reports the value it
actually used on ``WaitForTextResult.effective_timeout``, so the agent
learns the policy from the result rather than from a failed call.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

#: Environment variable that overrides the wait ceiling.
WAIT_MAX_SECONDS_ENV = "LIBTMUX_MCP_WAIT_MAX_SECONDS"

#: Default ceiling. Long enough for a slow build step to print a
#: marker, short enough that a wedged wait is an annoyance rather than
#: a hung agent session.
WAIT_MAX_SECONDS_DEFAULT = 30.0

#: Hard bounds on the operator override itself. A ceiling below 1 s
#: makes every wait useless; above 120 s it stops being a ceiling.
WAIT_MAX_SECONDS_FLOOR = 1.0
WAIT_MAX_SECONDS_LIMIT = 120.0

_wait_max_seconds: float = WAIT_MAX_SECONDS_DEFAULT


def _resolve_wait_max_seconds(value: str | None) -> float:
    """Return the effective wait ceiling for a ``LIBTMUX_MCP_WAIT_MAX_SECONDS``.

    Mirrors :func:`libtmux_mcp.server._resolve_safety_level`: never
    raises, warns on a bad value, falls back to a safe default.

    Parameters
    ----------
    value : str or None
        Raw environment value, or ``None`` when unset.

    Returns
    -------
    float
        Ceiling in seconds, clamped to
        ``[WAIT_MAX_SECONDS_FLOOR, WAIT_MAX_SECONDS_LIMIT]``.
    """
    if value is None:
        return WAIT_MAX_SECONDS_DEFAULT
    try:
        parsed = float(value)
    except ValueError:
        logger.warning(
            "invalid %s=%r, falling back to %.1fs",
            WAIT_MAX_SECONDS_ENV,
            value,
            WAIT_MAX_SECONDS_DEFAULT,
        )
        return WAIT_MAX_SECONDS_DEFAULT
    if not math.isfinite(parsed):
        logger.warning(
            "non-finite %s=%r, falling back to %.1fs",
            WAIT_MAX_SECONDS_ENV,
            value,
            WAIT_MAX_SECONDS_DEFAULT,
        )
        return WAIT_MAX_SECONDS_DEFAULT
    clamped = min(max(parsed, WAIT_MAX_SECONDS_FLOOR), WAIT_MAX_SECONDS_LIMIT)
    if clamped != parsed:
        logger.warning(
            "%s=%r out of range, clamped to %.1fs",
            WAIT_MAX_SECONDS_ENV,
            value,
            clamped,
        )
    return clamped


def _configure_wait_ceiling(seconds: float) -> None:
    """Publish the effective wait ceiling to the wait tool modules.

    Parameters
    ----------
    seconds : float
        Resolved ceiling; re-clamped defensively so a programmatic
        caller cannot install an unbounded value.
    """
    global _wait_max_seconds
    _wait_max_seconds = min(
        max(seconds, WAIT_MAX_SECONDS_FLOOR), WAIT_MAX_SECONDS_LIMIT
    )


def _wait_ceiling_seconds() -> float:
    """Return the currently published wait ceiling in seconds."""
    return _wait_max_seconds
