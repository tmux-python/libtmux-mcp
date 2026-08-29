"""Safety tiers, and the MCP annotations that publish them.

Every tool carries exactly one tier tag. `SafetyMiddleware` gates on the
tag, so a tool is governed by construction rather than by a name list.
"""

from __future__ import annotations

import logging
import typing as t

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Safety tier tags
# ---------------------------------------------------------------------------

TAG_READONLY = "readonly"
TAG_MUTATING = "mutating"
TAG_DESTRUCTIVE = "destructive"

VALID_SAFETY_LEVELS = frozenset({TAG_READONLY, TAG_MUTATING, TAG_DESTRUCTIVE})

#: Non-tier marker for tools that enforce their own wall-clock ceiling,
#: whose cost is therefore *duration* rather than side effects. Such a
#: tool must never be re-driven by machinery that assumes a cheap call:
#:
#: * :class:`~libtmux_mcp.middleware.ReadonlyRetryMiddleware` skips it --
#:   the deadline lives in the tool body, so a retry doubles the ceiling.
#: * The ``call_*_tools_batch`` wrappers reject it per-operation: the
#:   batch loop is serial with no aggregate deadline and
#:   ``MAX_BATCH_OPERATIONS`` is 1000.
#:
#: A tag rather than a name list because ``add_tool_transformation`` can
#: rename a tool out from under a name. Tier resolution reads only the
#: three tier tags, so this one is inert elsewhere.
TAG_SELF_BOUNDED = "self-bounded"

# ---------------------------------------------------------------------------
# Reusable annotation presets for tool registration
# ---------------------------------------------------------------------------

ANNOTATIONS_RO: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
ANNOTATIONS_MUTATING: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
ANNOTATIONS_CREATE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
#: Annotations for tools that move a user-supplied payload into a shell
#: context, whether directly (``send_keys``, ``run_command``,
#: ``paste_text``, ``pipe_pane``) or through a staged buffer
#: (``load_buffer`` then ``paste_buffer``).
#:
#: ``openWorldHint=True`` is what separates these from
#: :data:`ANNOTATIONS_CREATE`: the effect extends into whatever command
#: or content the caller supplies.
ANNOTATIONS_SHELL: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
ANNOTATIONS_DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}

#: Per-tool MCP ``meta`` hinting that a client keep this tool visible
#: rather than deferred. FastMCP passes ``meta`` opaquely and honouring it
#: is the client's business, so this is a safe no-op for one that does not
#: index the ``anthropic/*`` namespace. ``alwaysLoad`` is documented at
#: https://code.claude.com/docs/en/mcp, honoured from Claude Code 2.1.121.
#:
#: Apply only to read-tier discovery anchors -- ``list_panes``,
#: ``list_windows``, ``snapshot_pane`` -- because each always-loaded tool
#: spends a fixed schema budget in clients that do honour the hint.
DISCOVERY_META: dict[str, t.Any] = {
    "anthropic/alwaysLoad": True,
}
#: Annotations for tools that stay in the ``mutating`` tier -- so they
#: remain visible to default-profile agents -- but can still terminate a
#: process or lose state. ``respawn_pane`` and ``clear_pane`` are the
#: canonical users: shell recovery and scrollback cleanup are ordinary
#: agent work, while the hints keep disclosing the cost.
#:
#: Hint values match :data:`ANNOTATIONS_DESTRUCTIVE`, which is paired with
#: ``TAG_DESTRUCTIVE`` where this one is paired with ``TAG_MUTATING``. Two
#: names for identical hints, so the call site states which it means.
ANNOTATIONS_MUTATING_DESTRUCTIVE: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}
