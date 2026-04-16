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


def test_show_buffer_tail_preserves_on_truncation(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``show_buffer`` tail-preserves when content exceeds ``max_lines``.

    Regression guard: prior to bounded output, ``show_buffer`` returned
    ``stdout.decode()`` verbatim for an arbitrarily large staged buffer
    (``load_buffer`` has no byte cap). That let a single call dump an
    unbounded payload into the agent's context. The bounded path mirrors
    ``capture_pane``: oldest lines drop, ``content_truncated`` flips to
    ``True``, and ``content_truncated_lines`` reports how many were
    dropped so the caller can re-request with ``max_lines=None``.
    """
    del mcp_session
    payload = "\n".join(f"line-{i}" for i in range(20))
    ref = load_buffer(
        content=payload,
        logical_name="trunc",
        socket_name=mcp_server.socket_name,
    )
    try:
        seen = show_buffer(
            ref.buffer_name,
            max_lines=5,
            socket_name=mcp_server.socket_name,
        )
        assert seen.content_truncated is True
        assert seen.content_truncated_lines == 15
        # Tail preservation: the last 5 lines survive, the first 15 are gone.
        kept = seen.content.splitlines()
        assert kept == [f"line-{i}" for i in range(15, 20)]
    finally:
        delete_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)


def test_show_buffer_full_read_when_max_lines_none(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``max_lines=None`` disables truncation for full-buffer recovery."""
    del mcp_session
    payload = "\n".join(f"line-{i}" for i in range(50))
    ref = load_buffer(
        content=payload,
        logical_name="full",
        socket_name=mcp_server.socket_name,
    )
    try:
        seen = show_buffer(
            ref.buffer_name,
            max_lines=None,
            socket_name=mcp_server.socket_name,
        )
        assert seen.content_truncated is False
        assert seen.content_truncated_lines == 0
        assert seen.content.rstrip("\n") == payload
    finally:
        delete_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)


def test_show_buffer_no_truncation_under_cap(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Small buffers are returned verbatim with truncation flags off."""
    del mcp_session
    payload = "one\ntwo\nthree"
    ref = load_buffer(
        content=payload,
        logical_name="small",
        socket_name=mcp_server.socket_name,
    )
    try:
        seen = show_buffer(
            ref.buffer_name,
            max_lines=100,
            socket_name=mcp_server.socket_name,
        )
        assert seen.content_truncated is False
        assert seen.content_truncated_lines == 0
        assert seen.content.rstrip("\n") == payload
    finally:
        delete_buffer(ref.buffer_name, socket_name=mcp_server.socket_name)


@pytest.mark.parametrize(
    ("tool_name", "match_text"),
    [
        ("load_buffer", "load-buffer timeout"),
        ("show_buffer", "show-buffer timeout"),
        ("delete_buffer", "delete-buffer timeout"),
    ],
)
def test_buffer_subprocess_timeout_surfaces_as_tool_error(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    match_text: str,
) -> None:
    """Hung tmux raises ``TimeoutExpired`` → clear ``ToolError``.

    Regression guard: previously each buffer tool caught only
    ``CalledProcessError``, so a ``subprocess.TimeoutExpired`` from the
    5-second cap would escape through ``handle_tool_errors`` and
    surface as a generic ``"Unexpected error: TimeoutExpired"``. The
    new per-tool handler reports the operation name, the 5-second
    cap, and the target buffer name.
    """
    import subprocess

    del mcp_session

    def _hang(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd="tmux", timeout=5.0)

    monkeypatch.setattr("libtmux_mcp.tools.buffer_tools.subprocess.run", _hang)

    tools: dict[str, t.Callable[..., t.Any]] = {
        "load_buffer": load_buffer,
        "show_buffer": show_buffer,
        "delete_buffer": delete_buffer,
    }
    fn = tools[tool_name]
    kwargs: dict[str, t.Any] = {"socket_name": mcp_server.socket_name}
    if tool_name == "load_buffer":
        kwargs.update({"content": "hang-test"})
    else:
        # show/delete need a valid MCP-namespaced buffer name so the
        # validator doesn't intercept before the subprocess is called.
        kwargs.update({"buffer_name": "libtmux_mcp_" + ("0" * 32) + "_x"})

    with pytest.raises(ToolError, match=match_text):
        fn(**kwargs)
