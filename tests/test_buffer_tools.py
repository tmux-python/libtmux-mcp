"""Tests for agent-namespaced tmux paste buffer tools."""

from __future__ import annotations

import typing as t

import pytest
from fastmcp.exceptions import ToolError

from libtmux_mcp.middleware import _SENSITIVE_ARG_NAMES
from libtmux_mcp.models import BufferContent, BufferRef
from libtmux_mcp.tools.buffer_tools import (
    _validate_buffer_name,
    _validate_logical_name,
    delete_buffer,
    load_buffer,
    paste_buffer,
    show_buffer,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session


@pytest.mark.parametrize("name", ["my-buffer", "clipboard.v2", "a", "x" * 64])
def test_logical_name_accepts_valid(name: str) -> None:
    """Valid logical names pass through."""
    assert _validate_logical_name(name) == name


def test_logical_name_empty_defaults_to_buf() -> None:
    """Empty logical names fall back to the ``buf`` placeholder."""
    assert _validate_logical_name("") == "buf"


@pytest.mark.parametrize(
    "name",
    ["has space", "with/slash", "x" * 65, "semi;colon"],
)
def test_logical_name_rejects_invalid(name: str) -> None:
    """Invalid logical names raise ToolError with the name quoted."""
    with pytest.raises(ToolError, match="Invalid logical buffer name"):
        _validate_logical_name(name)


def test_buffer_name_rejects_non_namespaced() -> None:
    """Names outside the MCP namespace are rejected."""
    with pytest.raises(ToolError, match="Invalid buffer name"):
        _validate_buffer_name("clipboard")


def test_buffer_name_accepts_full_shape() -> None:
    """A well-formed MCP buffer name passes validation."""
    name = "libtmux_mcp_00112233445566778899aabbccddeeff_buf"
    assert _validate_buffer_name(name) == name


def test_load_buffer_allocates_unique_name(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Two ``load_buffer`` calls produce distinct buffers.

    The UUID nonce in the allocated name prevents collisions even when
    the caller reuses the same logical name.
    """
    del mcp_session
    a = load_buffer(
        content="first",
        logical_name="clipboard",
        socket_name=mcp_server.socket_name,
    )
    b = load_buffer(
        content="second",
        logical_name="clipboard",
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(a, BufferRef)
    assert isinstance(b, BufferRef)
    assert a.buffer_name != b.buffer_name
    assert a.buffer_name.startswith("libtmux_mcp_")
    assert a.buffer_name.endswith("_clipboard")

    # Clean up — buffers are server-global; leaking them would pollute
    # the test run even though the libtmux Server fixture is torn down.
    delete_buffer(a.buffer_name, socket_name=mcp_server.socket_name)
    delete_buffer(b.buffer_name, socket_name=mcp_server.socket_name)


def test_buffer_round_trip(mcp_server: Server, mcp_session: Session) -> None:
    """Load -> show -> delete round-trip preserves content exactly."""
    del mcp_session
    payload = "line1\nline2\nline3"
    ref = load_buffer(
        content=payload,
        logical_name="roundtrip",
        socket_name=mcp_server.socket_name,
    )
    seen = show_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)
    assert isinstance(seen, BufferContent)
    assert seen.buffer_name == ref.buffer_name
    # tmux load-buffer preserves trailing newline behaviour; strip for
    # comparison.
    assert seen.content.rstrip("\n") == payload

    result = delete_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)
    assert "deleted" in result

    with pytest.raises(ToolError, match="show-buffer failed"):
        show_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)


def test_paste_buffer_requires_mcp_namespace(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``paste_buffer`` refuses to paste a non-MCP buffer."""
    with pytest.raises(ToolError, match="Invalid buffer name"):
        paste_buffer(
            buffer_name="clipboard",
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )


def test_paste_buffer_into_pane(mcp_server: Server, mcp_pane: Pane) -> None:
    """``paste_buffer`` pastes an MCP buffer into the target pane."""
    ref = load_buffer(
        content="echo PASTE_BUFFER_MARKER",
        logical_name="smoke",
        socket_name=mcp_server.socket_name,
    )
    try:
        result = paste_buffer(
            buffer_name=ref.buffer_name,
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
        assert ref.buffer_name in result
    finally:
        delete_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)


def test_content_is_in_sensitive_args() -> None:
    """``content`` is redacted by the audit middleware."""
    assert "content" in _SENSITIVE_ARG_NAMES
