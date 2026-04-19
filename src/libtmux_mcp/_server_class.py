"""FastMCP subclass that injects JSON repair into the stdio transport.

Motivation and architectural context: see :mod:`libtmux_mcp._json_repair`
and `tmux-python/libtmux-mcp#17
<https://github.com/tmux-python/libtmux-mcp/issues/17>`_.

Why a subclass rather than a monkey-patch
-----------------------------------------

FastMCP's server-side has no pluggable transport abstraction
(`Transport = Literal["stdio", "http", "sse", "streamable-http"]`
at ``fastmcp/server/server.py`` — hard-coded enum, no ABC, no
registry). Middleware fires post-parse; lifespan runs outside the
stream loop; pydantic v2 exposes no pre-JSON-parse hook. The
*only* clean hook is the MCP SDK's public
``stdio_server(stdin=..., stdout=...)`` API — but FastMCP's own
``run_stdio_async`` calls it with no arguments and does not thread
the passthrough through.

Subclassing `FastMCP` and overriding ``run_stdio_async`` is
standard Python extension. The override below replicates upstream's
body almost verbatim (see the reference citation in
:meth:`LibtmuxMcpServer.run_stdio_async`); the only behavioural
difference is the ``stdin=`` kwarg passed to ``stdio_server``. No
library global is mutated; the MCP SDK's ``stdio_server(stdin=...)``
is a public, typed API we're using as documented.

Follow-up (not filed here)
--------------------------

Consider filing a request at
`jlowin/fastmcp <https://github.com/jlowin/fastmcp>`_ to thread
``stdin=`` / ``stdout=`` params through
``FastMCP.run_stdio_async()``. Under-10-line upstream change. When
merged and the FastMCP floor bumps past it, this subclass collapses
to a one-line ``super().run_stdio_async(..., stdin=...)`` call and
the body duplication disappears.

Fragility acknowledgement
-------------------------

The override touches three private-by-underscore FastMCP
attributes — ``_lifespan_manager`` and ``_mcp_server`` on the
instance, plus ``create_initialization_options`` via the latter.
These are internal API; a FastMCP rename triggers an
``AttributeError`` at import, which is the correct loud-failure
mode. The project pins ``fastmcp>=3.2.4`` in ``pyproject.toml``;
the private-attribute contract is verified for that floor.
"""

from __future__ import annotations

import os
import sys
import typing as t
from io import TextIOWrapper

import anyio
from fastmcp import FastMCP
from fastmcp.server.context import reset_transport, set_transport
from fastmcp.utilities.cli import log_server_banner
from fastmcp.utilities.logging import get_logger, temporary_log_level
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.stdio import stdio_server

from libtmux_mcp._json_repair import _RepairingStdin

logger = get_logger(__name__)

#: Environment variable that disables the repair at startup. Set to
#: any truthy value (``"1"``, ``"true"``, anything non-empty) to fall
#: back to FastMCP's default stdio transport verbatim. Useful for
#: reproducing client-side bugs without the server's repair layer in
#: the way.
_DISABLE_ENV_VAR: str = "LIBTMUX_MCP_DISABLE_JSON_REPAIR"


class LibtmuxMcpServer(FastMCP):
    """FastMCP server with transport-layer JSON repair for buggy clients.

    Overrides :meth:`fastmcp.FastMCP.run_stdio_async` to pass a
    :class:`libtmux_mcp._json_repair._RepairingStdin` into
    :func:`mcp.server.stdio.stdio_server`. All other behaviour is
    inherited from FastMCP unchanged — ``IS-A FastMCP`` is preserved,
    so every existing call site that works with ``FastMCP(...)``
    continues to work with ``LibtmuxMcpServer(...)``.
    """

    async def run_stdio_async(
        self,
        show_banner: bool = True,
        log_level: str | None = None,
        stateless: bool = False,
    ) -> None:
        """Serve MCP over stdio, wrapping stdin with the JSON-repair layer.

        Mirrors :meth:`fastmcp.FastMCP.run_stdio_async` so the
        initialization, banner, log-level, lifespan, and transport
        context-var semantics are identical to upstream. The only
        behavioural difference is passing ``stdin=_RepairingStdin(...)``
        to :func:`stdio_server`.

        Respects :data:`_DISABLE_ENV_VAR` (``LIBTMUX_MCP_DISABLE_JSON_REPAIR``):
        when set, delegates to :meth:`super().run_stdio_async` unchanged
        so the repair layer is out of the picture entirely.

        Notes
        -----
        Reference body: ``fastmcp/server/mixins/transport.py:184-224``
        at ``fastmcp==3.2.4``. On FastMCP bump, diff this method
        against upstream and reconcile any semantics drift.
        """
        if os.environ.get(_DISABLE_ENV_VAR):
            return await super().run_stdio_async(
                show_banner=show_banner,
                log_level=log_level,
                stateless=stateless,
            )

        # Display server banner — upstream uses the module-level helper.
        if show_banner:
            log_server_banner(server=self)

        token = set_transport("stdio")
        try:
            with temporary_log_level(log_level):
                async with self._lifespan_manager():
                    # Build the repair-wrapping stdin. The underlying
                    # construction (``anyio.wrap_file(TextIOWrapper(...))``)
                    # mirrors the MCP SDK's own default path at
                    # ``mcp/server/stdio.py:46-47`` so we preserve the
                    # exact encoding / errors behaviour, differing only
                    # in that each yielded line passes through
                    # :func:`repair_unquoted_string_values` first.
                    base_stdin = anyio.wrap_file(
                        TextIOWrapper(
                            sys.stdin.buffer,
                            encoding="utf-8",
                            errors="replace",
                        )
                    )
                    repairing_stdin = t.cast(
                        "anyio.AsyncFile[str]",
                        _RepairingStdin(base_stdin),
                    )
                    async with stdio_server(stdin=repairing_stdin) as (
                        read_stream,
                        write_stream,
                    ):
                        mode = " (stateless)" if stateless else ""
                        logger.info(
                            f"Starting MCP server {self.name!r} with "
                            f"transport 'stdio'{mode} (+JSON repair)"
                        )
                        await self._mcp_server.run(
                            read_stream,
                            write_stream,
                            self._mcp_server.create_initialization_options(
                                notification_options=NotificationOptions(
                                    tools_changed=True
                                ),
                            ),
                            stateless=stateless,
                        )
        finally:
            reset_transport(token)
