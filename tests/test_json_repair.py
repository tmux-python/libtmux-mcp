"""Tests for the malformed-JSON repair layer (#17).

Coverage:

- Pure string transform (``repair_unquoted_string_values``) — positive
  repair cases and passthrough cases as parametrized fixtures.
- :class:`_RepairingStdin` — async-iterable wrapper sanity: EOF
  termination, valid-line passthrough, malformed-line repair.
- Integration: repaired payload parses as a valid
  :class:`mcp.types.JSONRPCMessage`.
- Opt-out: ``LIBTMUX_MCP_DISABLE_JSON_REPAIR=1`` delegates to
  :meth:`FastMCP.run_stdio_async` unchanged (verified via attribute
  introspection rather than a live stdio spawn).
"""

from __future__ import annotations

import asyncio
import io
import os
import typing as t

import anyio
import pytest

from libtmux_mcp._json_repair import (
    _RepairingStdin,
    repair_unquoted_string_values,
)

if t.TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# repair_unquoted_string_values — pure string transform
# ---------------------------------------------------------------------------


class UnquotedValueFixture(t.NamedTuple):
    """Parametrization fixture for the quote-repair function."""

    test_id: str
    source: str
    expected: str


_REPAIR_FIXTURES: list[UnquotedValueFixture] = [
    UnquotedValueFixture(
        test_id="bare_session_id_dollar",
        source='{"session_id": $10}',
        expected='{"session_id": "$10"}',
    ),
    UnquotedValueFixture(
        test_id="bare_pane_id_percent",
        source='{"pane_id": %1}',
        expected='{"pane_id": "%1"}',
    ),
    UnquotedValueFixture(
        test_id="bare_window_id_at",
        source='{"window_id": @5}',
        expected='{"window_id": "@5"}',
    ),
    UnquotedValueFixture(
        test_id="bare_identifier_session_name",
        source='{"session_name": cv}',
        expected='{"session_name": "cv"}',
    ),
    UnquotedValueFixture(
        test_id="dotted_identifier",
        source='{"session_name": my.session.v2}',
        expected='{"session_name": "my.session.v2"}',
    ),
    UnquotedValueFixture(
        test_id="hyphenated_identifier_key_binding",
        source='{"keys": C-c}',
        expected='{"keys": "C-c"}',
    ),
    UnquotedValueFixture(
        test_id="multiple_bare_values_one_object",
        source='{"session_id": $10, "pane_id": %1}',
        expected='{"session_id": "$10", "pane_id": "%1"}',
    ),
    UnquotedValueFixture(
        test_id="nested_object_inner_bare_value",
        source='{"outer": {"session_id": $10}}',
        expected='{"outer": {"session_id": "$10"}}',
    ),
    UnquotedValueFixture(
        test_id="trailing_closing_brace",
        source='{"id": $10}',
        expected='{"id": "$10"}',
    ),
    UnquotedValueFixture(
        test_id="preserves_surrounding_whitespace",
        source='{ "session_id" :   $10  }',
        expected='{ "session_id" :   "$10"  }',
    ),
]

_PASSTHROUGH_FIXTURES: list[UnquotedValueFixture] = [
    UnquotedValueFixture(
        test_id="integer_value_untouched",
        source='{"count": 42}',
        expected='{"count": 42}',
    ),
    UnquotedValueFixture(
        test_id="float_value_untouched",
        source='{"ratio": 0.5}',
        expected='{"ratio": 0.5}',
    ),
    UnquotedValueFixture(
        test_id="true_keyword_untouched",
        source='{"attached": true}',
        expected='{"attached": true}',
    ),
    UnquotedValueFixture(
        test_id="false_keyword_untouched",
        source='{"muted": false}',
        expected='{"muted": false}',
    ),
    UnquotedValueFixture(
        test_id="null_keyword_untouched",
        source='{"parent": null}',
        expected='{"parent": null}',
    ),
    UnquotedValueFixture(
        test_id="already_quoted_string_untouched",
        source='{"name": "already quoted"}',
        expected='{"name": "already quoted"}',
    ),
    UnquotedValueFixture(
        test_id="empty_object_untouched",
        source="{}",
        expected="{}",
    ),
    UnquotedValueFixture(
        test_id="canonical_mcp_frame_untouched",
        source='{"tool": "list_windows", "args": {"pane_id": "%1"}}',
        expected='{"tool": "list_windows", "args": {"pane_id": "%1"}}',
    ),
    UnquotedValueFixture(
        test_id="mixed_valid_and_keywords",
        source='{"a": 1, "b": "two", "c": true, "d": null}',
        expected='{"a": 1, "b": "two", "c": true, "d": null}',
    ),
]


@pytest.mark.parametrize(
    UnquotedValueFixture._fields,
    _REPAIR_FIXTURES,
    ids=[f.test_id for f in _REPAIR_FIXTURES],
)
def test_repair_quotes_unquoted_values(
    test_id: str,
    source: str,
    expected: str,
) -> None:
    """Each unquoted-value class is repaired to canonical JSON."""
    assert repair_unquoted_string_values(source) == expected


@pytest.mark.parametrize(
    UnquotedValueFixture._fields,
    _PASSTHROUGH_FIXTURES,
    ids=[f.test_id for f in _PASSTHROUGH_FIXTURES],
)
def test_repair_passes_valid_json_through_unchanged(
    test_id: str,
    source: str,
    expected: str,
) -> None:
    """Valid JSON — numbers, keywords, quoted strings — is untouched."""
    assert repair_unquoted_string_values(source) == expected


# ---------------------------------------------------------------------------
# _RepairingStdin — async-iterable wrapper
# ---------------------------------------------------------------------------


def _wrap_stringio(buf: io.StringIO) -> anyio.AsyncFile[str]:
    """Build an ``anyio.AsyncFile[str]`` over an in-memory buffer.

    Mirrors the MCP SDK's construction: wrap a text-mode file-like in
    ``anyio.wrap_file`` so ``async for line`` yields lines. Typed as
    ``AsyncFile[str]`` because the buffer is text mode.
    """
    return t.cast("anyio.AsyncFile[str]", anyio.wrap_file(buf))


async def _collect_lines(wrapper: _RepairingStdin) -> list[str]:
    return [line async for line in wrapper]


def test_stream_wrapper_repairs_malformed_lines() -> None:
    """Each yielded line passes through the repair function."""
    buf = io.StringIO('{"session_id": $10}\n{"session_name": cv}\n{"attached": true}\n')
    wrapper = _RepairingStdin(_wrap_stringio(buf))

    result = asyncio.run(_collect_lines(wrapper))

    assert result == [
        '{"session_id": "$10"}\n',
        '{"session_name": "cv"}\n',
        '{"attached": true}\n',
    ]


def test_stream_wrapper_terminates_at_eof() -> None:
    """Iteration stops cleanly at end-of-stream."""
    buf = io.StringIO("")
    wrapper = _RepairingStdin(_wrap_stringio(buf))

    assert asyncio.run(_collect_lines(wrapper)) == []


def test_stream_wrapper_passes_valid_json_unchanged() -> None:
    """Already-canonical JSON is byte-for-byte identical post-wrap."""
    canonical = '{"tool": "list_windows", "args": {"pane_id": "%1"}}\n'
    buf = io.StringIO(canonical)
    wrapper = _RepairingStdin(_wrap_stringio(buf))

    assert asyncio.run(_collect_lines(wrapper)) == [canonical]


# ---------------------------------------------------------------------------
# Integration: repaired payload parses as a valid JSONRPCMessage
# ---------------------------------------------------------------------------


def test_repaired_frame_parses_as_jsonrpc_message() -> None:
    """The typical Cursor-mangled frame becomes a valid JSON-RPC request.

    Regression lock: running the mangled frame through the repair
    function produces text that ``mcp.types.JSONRPCMessage``
    validates without error, and the arguments come out as the
    string the tool expected (``"$10"``, not ``$10``).
    """
    from mcp.types import JSONRPCMessage, JSONRPCRequest

    mangled = (
        '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", '
        '"params": {"name": "list_windows", '
        '"arguments": {"session_id": $10}}}'
    )

    repaired = repair_unquoted_string_values(mangled)
    msg = JSONRPCMessage.model_validate_json(repaired)

    assert isinstance(msg.root, JSONRPCRequest)
    assert msg.root.params is not None
    assert msg.root.params["arguments"] == {"session_id": "$10"}


# ---------------------------------------------------------------------------
# Opt-out: LIBTMUX_MCP_DISABLE_JSON_REPAIR
# ---------------------------------------------------------------------------


def test_subclass_is_a_fastmcp() -> None:
    """``LibtmuxMcpServer`` IS-A ``FastMCP`` — preserves call-site API."""
    from fastmcp import FastMCP

    from libtmux_mcp._server_class import LibtmuxMcpServer

    assert issubclass(LibtmuxMcpServer, FastMCP)


def test_subclass_overrides_run_stdio_async() -> None:
    """The override is present on the subclass, not inherited."""
    from fastmcp import FastMCP

    from libtmux_mcp._server_class import LibtmuxMcpServer

    assert LibtmuxMcpServer.run_stdio_async is not FastMCP.run_stdio_async, (
        "LibtmuxMcpServer must provide its own run_stdio_async"
    )


def test_opt_out_env_var_recognized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting ``LIBTMUX_MCP_DISABLE_JSON_REPAIR`` takes the super() path.

    We don't actually boot stdio — we patch ``FastMCP.run_stdio_async``
    on the subclass and verify the override routes to it when the
    env var is set.
    """
    from fastmcp import FastMCP

    from libtmux_mcp._server_class import LibtmuxMcpServer

    sentinel = object()
    calls: list[dict[str, t.Any]] = []

    async def _fake_super_run(
        self: FastMCP,
        show_banner: bool = True,
        log_level: str | None = None,
        stateless: bool = False,
    ) -> object:
        calls.append(
            {
                "show_banner": show_banner,
                "log_level": log_level,
                "stateless": stateless,
            }
        )
        return sentinel

    monkeypatch.setattr(FastMCP, "run_stdio_async", _fake_super_run)
    monkeypatch.setenv("LIBTMUX_MCP_DISABLE_JSON_REPAIR", "1")

    server = LibtmuxMcpServer(name="opt-out-test")
    result = asyncio.run(
        server.run_stdio_async(show_banner=False, log_level="DEBUG", stateless=True)
    )

    assert result is sentinel
    assert calls == [{"show_banner": False, "log_level": "DEBUG", "stateless": True}]


def test_opt_out_absent_does_not_use_super(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the env var, the override does not fall through to super().

    Asserts the subclass's own body is invoked rather than the parent's.
    We short-circuit before stdio_server actually starts by raising
    from ``log_server_banner``.
    """
    from fastmcp import FastMCP

    from libtmux_mcp._server_class import LibtmuxMcpServer

    super_called = False

    async def _super_run(*args: t.Any, **kwargs: t.Any) -> None:
        nonlocal super_called
        super_called = True

    class _Sentinel(Exception):
        pass

    def _raise_sentinel(**_: t.Any) -> None:
        raise _Sentinel

    monkeypatch.setattr(FastMCP, "run_stdio_async", _super_run)
    monkeypatch.delenv("LIBTMUX_MCP_DISABLE_JSON_REPAIR", raising=False)
    monkeypatch.setattr("libtmux_mcp._server_class.log_server_banner", _raise_sentinel)

    server = LibtmuxMcpServer(name="override-test")

    with pytest.raises(_Sentinel):
        asyncio.run(server.run_stdio_async())

    assert super_called is False, (
        "Without the opt-out env var, the subclass must run its own body"
    )


def test_disable_env_var_constant_is_public_name() -> None:
    """Operators reading the docs can import the constant."""
    from libtmux_mcp._server_class import _DISABLE_ENV_VAR

    assert _DISABLE_ENV_VAR == "LIBTMUX_MCP_DISABLE_JSON_REPAIR"
    # Sanity: env var is actually read from the process env at runtime.
    # We can't run the full coroutine here, but the constant must match
    # what the code consults so operators can trust their setenv calls.
    _ = os.environ.get(_DISABLE_ENV_VAR)  # exercise the lookup
