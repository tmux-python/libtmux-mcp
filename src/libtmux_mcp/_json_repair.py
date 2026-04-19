"""Conservative JSON repair for malformed MCP frames.

Some MCP clients (tracked: Cursor agent Auto Mode on specific
model paths) intermittently serialize tool arguments with string
values emitted unquoted — e.g. ``{"session_id": $10}`` instead of
``{"session_id": "$10"}``. Such frames are rejected at the MCP stdio
transport's JSON-parse step (``mcp/server/stdio.py:65``) before any
FastMCP middleware or libtmux-mcp handler runs, silently dropping
the request. See tmux-python/libtmux-mcp#17.

This module provides:

- :func:`repair_unquoted_string_values` — pure string transform that
  quotes the specific unquoted-identifier patterns we have observed.
  Never touches valid JSON; never quotes JSON keywords
  (``true`` / ``false`` / ``null``).
- :class:`_RepairingStdin` — async-iterable stdin wrapper that yields
  repair-passed lines. Designed to be passed as the ``stdin=``
  argument to :func:`mcp.server.stdio.stdio_server` (a public,
  documented MCP SDK API).

Pairing lives in :class:`libtmux_mcp._server_class.LibtmuxMcpServer`,
a :class:`fastmcp.FastMCP` subclass that overrides
``run_stdio_async`` to wire the stream wrapper through
``stdio_server(stdin=_RepairingStdin(base_stdin))``. No library
globals are mutated; the override uses only public MCP SDK surface
and standard Python subclassing.

Opt-out: set ``LIBTMUX_MCP_DISABLE_JSON_REPAIR=1`` in the server's
environment to bypass the wrap and use FastMCP's default transport
verbatim (useful for reproducing client bugs cleanly).
"""

from __future__ import annotations

import re
import typing as t

import anyio

#: Pattern for ``"key": <unquoted-value>`` pairs the repair targets.
#:
#: Group 1 captures ``"key"`` plus the colon and surrounding
#: whitespace so the replacement can reuse it verbatim. Group 2
#: captures the bare value that needs quoting:
#:
#: * ``[$%@][\w-]+`` — tmux ID forms (``$10``, ``%1``, ``@5``).
#: * ``[A-Za-z_][\w.-]*`` — plain identifier-shaped session / window
#:   names (``cv``, ``my.session.v2``, ``C-c``).
#:
#: The positive lookahead on ``[,}\]]`` anchors the end so
#: already-quoted strings cannot accidentally match, and the match
#: stops at a JSON terminator. Numbers don't match because neither
#: alternative starts with a digit.
_UNQUOTED_VALUE_RE: re.Pattern[str] = re.compile(
    r'("[A-Za-z_][\w.-]*"\s*:\s*)'
    r"([$%@][\w-]+|[A-Za-z_][\w.-]*)"
    r"(?=\s*[,}\]])"
)

#: JSON keywords the replacement callback must never quote.
_JSON_KEYWORDS: frozenset[str] = frozenset({"true", "false", "null"})


def repair_unquoted_string_values(payload: str) -> str:
    """Quote unquoted string values emitted by buggy MCP clients.

    Scope: ``"key": VALUE`` pairs where VALUE is an unquoted tmux ID
    (``$10`` / ``%1`` / ``@5``) or bare identifier (``cv``,
    ``my.session``, ``C-c``) followed by a JSON terminator
    (``,``, ``}``, ``]``). JSON keywords (``true`` / ``false`` /
    ``null``) and all numbers are skipped. Quoted strings are
    untouched because the regex requires an unquoted token start.

    Non-goals: trailing commas, missing braces, escaped-string
    corruption. Valid JSON is a fixed point of this function.

    Parameters
    ----------
    payload : str
        Raw JSON text, possibly containing unquoted string values.

    Returns
    -------
    str
        The same text with identified unquoted values quoted. If no
        matches fire, the input is returned unchanged.

    Examples
    --------
    >>> repair_unquoted_string_values('{"session_id": $10}')
    '{"session_id": "$10"}'
    >>> repair_unquoted_string_values('{"session_name": cv}')
    '{"session_name": "cv"}'
    >>> repair_unquoted_string_values('{"attached": true}')
    '{"attached": true}'
    >>> repair_unquoted_string_values('{"count": 42}')
    '{"count": 42}'
    """

    def _quote(match: re.Match[str]) -> str:
        prefix, value = match.group(1), match.group(2)
        if value in _JSON_KEYWORDS:
            return match.group(0)
        return f'{prefix}"{value}"'

    return _UNQUOTED_VALUE_RE.sub(_quote, payload)


class _RepairingStdin:
    """Async-iterable wrapper yielding repair-passed lines from stdin.

    The MCP SDK's :func:`mcp.server.stdio.stdio_server` consumes its
    ``stdin`` argument exclusively via ``async for line in stdin``
    (see ``mcp/server/stdio.py:63``). A minimal async iterable
    therefore satisfies the contract; a full ``anyio.AsyncFile``
    surface is not required.

    The SDK's ``stdin`` parameter is typed as ``anyio.AsyncFile[str]``
    so callers pass this wrapper via :func:`typing.cast` — the cast
    is justified by the duck-typing argument above.

    Parameters
    ----------
    wrapped : anyio.AsyncFile[str]
        The underlying async-file-like stdin stream the MCP SDK
        would otherwise read directly. Typically built via
        ``anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, ...))`` —
        matching the MCP SDK's own default construction.
    """

    _wrapped: anyio.AsyncFile[str]

    def __init__(self, wrapped: anyio.AsyncFile[str]) -> None:
        self._wrapped = wrapped

    def __aiter__(self) -> t.AsyncIterator[str]:
        return self._iter_lines()

    async def _iter_lines(self) -> t.AsyncIterator[str]:
        async for line in self._wrapped:
            yield repair_unquoted_string_values(line)
