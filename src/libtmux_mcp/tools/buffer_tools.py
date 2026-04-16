"""Agent-namespaced tmux paste buffer tools.

Tmux paste buffers are server-global: every buffer lives in a single
flat namespace shared by all clients on that tmux server. If two MCP
agents — or two parallel tool calls from one agent — independently
created a buffer named ``clipboard`` they would silently overwrite
each other's content.

To make buffers safe for concurrent use, every ``load_buffer`` call
allocates a unique name of the form::

    libtmux_mcp_<uuid4hex>_<logical_name>

and returns the full name in a :class:`BufferRef` so the caller can
round-trip with :func:`paste_buffer`, :func:`show_buffer`, and
:func:`delete_buffer` without ambiguity.

``list_buffers`` is **not** exposed in the default safety tier —
buffer contents often include the user's OS clipboard history (passwords,
private snippets), and a blanket enumeration would leak that to the
agent. Callers track the buffers they own via the ``BufferRef``s
returned from ``load_buffer``.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import tempfile
import typing as t
import uuid

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    ANNOTATIONS_MUTATING,
    ANNOTATIONS_RO,
    ANNOTATIONS_SHELL,
    TAG_MUTATING,
    TAG_READONLY,
    _get_server,
    _resolve_pane,
    _tmux_argv,
    handle_tool_errors,
)
from libtmux_mcp.models import BufferContent, BufferRef
from libtmux_mcp.tools.pane_tools.io import (
    CAPTURE_DEFAULT_MAX_LINES,
    _truncate_lines_tail,
)

#: Default line cap for :func:`show_buffer`. Reuses the scrollback
#: default so agents see one consistent bound across read-heavy tools.
SHOW_BUFFER_DEFAULT_MAX_LINES = CAPTURE_DEFAULT_MAX_LINES

if t.TYPE_CHECKING:
    from fastmcp import FastMCP

#: Reserved prefix for MCP-allocated buffers. Anything matching this
#: regex is considered agent-owned; anything else is the human user's
#: buffer (including OS-clipboard sync buffers) and must not be exposed.
_MCP_BUFFER_PREFIX = "libtmux_mcp_"

#: Full-shape validator for MCP-allocated buffer names. Caller-provided
#: logical names are restricted to a conservative alphabet so the final
#: name is stable and safe to pass to ``tmux load-buffer -b``.
_BUFFER_NAME_RE = re.compile(
    r"^libtmux_mcp_[0-9a-f]{32}_[A-Za-z0-9_.-]{1,64}$",
)

#: Validator for the caller-supplied logical portion of a buffer name.
#: Empty logical names are replaced with ``buf`` to avoid a trailing
#: underscore in the allocated name.
_LOGICAL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _validate_logical_name(name: str) -> str:
    """Return ``name`` unchanged if it is a valid logical portion.

    Empty strings collapse to ``"buf"`` before validation because
    tmux-side buffer names must contain at least one character after
    the UUID separator.

    Examples
    --------
    >>> _validate_logical_name("my-buffer")
    'my-buffer'
    >>> _validate_logical_name("clipboard.v2")
    'clipboard.v2'
    >>> _validate_logical_name("")
    'buf'
    >>> _validate_logical_name("has space")
    Traceback (most recent call last):
    ...
    fastmcp.exceptions.ToolError: Invalid logical buffer name: 'has space'
    >>> _validate_logical_name("with/slash")
    Traceback (most recent call last):
    ...
    fastmcp.exceptions.ToolError: Invalid logical buffer name: 'with/slash'
    """
    if name == "":
        return "buf"
    if not _LOGICAL_NAME_RE.fullmatch(name):
        msg = f"Invalid logical buffer name: {name!r}"
        raise ToolError(msg)
    return name


def _validate_buffer_name(name: str) -> str:
    """Return ``name`` unchanged if it is a well-formed MCP buffer name.

    Rejects names outside the MCP namespace so the tool surface cannot
    be tricked into reading or clobbering buffers the agent did not
    allocate. This is the main defence against the "clipboard privacy"
    risk documented at the module level.

    Examples
    --------
    >>> _validate_buffer_name("libtmux_mcp_00112233445566778899aabbccddeeff_buf")
    'libtmux_mcp_00112233445566778899aabbccddeeff_buf'
    >>> _validate_buffer_name("clipboard")
    Traceback (most recent call last):
    ...
    fastmcp.exceptions.ToolError: Invalid buffer name: 'clipboard'
    >>> _validate_buffer_name("libtmux_mcp_shortuuid_buf")
    Traceback (most recent call last):
    ...
    fastmcp.exceptions.ToolError: Invalid buffer name: 'libtmux_mcp_shortuuid_buf'
    """
    if not _BUFFER_NAME_RE.fullmatch(name):
        msg = f"Invalid buffer name: {name!r}"
        raise ToolError(msg)
    return name


def _allocate_buffer_name(logical_name: str | None) -> str:
    """Allocate a unique MCP buffer name for a caller's logical label.

    The returned name always has the shape
    ``libtmux_mcp_<32-hex-uuid>_<logical_name>`` — the prefix defends
    the tool surface against interacting with buffers it did not
    create (OS-clipboard sync populates tmux's server-global namespace
    too), and the uuid nonce prevents collisions when multiple agents
    or parallel tool calls allocate buffers at the same time. When
    ``logical_name`` is empty or ``None``, ``"buf"`` is substituted
    to avoid a trailing-underscore name.

    Examples
    --------
    >>> name = _allocate_buffer_name("clip")
    >>> name.startswith("libtmux_mcp_")
    True
    >>> name.endswith("_clip")
    True
    >>> # 32 hex characters between the prefix and the logical suffix.
    >>> len(name.removeprefix("libtmux_mcp_").rsplit("_", 1)[0])
    32

    Empty logical name collapses to ``"buf"``:

    >>> _allocate_buffer_name("").endswith("_buf")
    True
    >>> _allocate_buffer_name(None).endswith("_buf")
    True
    """
    base = _validate_logical_name(logical_name or "")
    return f"{_MCP_BUFFER_PREFIX}{uuid.uuid4().hex}_{base}"


@handle_tool_errors
def load_buffer(
    content: str,
    logical_name: str | None = None,
    socket_name: str | None = None,
) -> BufferRef:
    """Load text into a new agent-namespaced tmux paste buffer.

    Each call allocates a fresh buffer name — two concurrent calls will
    land in distinct buffers even if they pass the same ``logical_name``.
    Agents MUST use the returned :attr:`BufferRef.buffer_name` on
    subsequent paste/show/delete calls.

    **When to use this vs. paste_text:** ``load_buffer`` is the
    stage-then-fire path — you get a handle back and can inspect via
    ``show_buffer``, paste into multiple panes via ``paste_buffer``,
    or hold the content for later. Use ``paste_text`` for a simple
    one-shot paste with no follow-up.

    Parameters
    ----------
    content : str
        The text to stage. Can be multi-line. Redacted in audit logs.
    logical_name : str, optional
        Short label for the buffer. Limited to
        ``[A-Za-z0-9_.-]{1,64}`` so the final name stays safe on the
        tmux command line. Empty or ``None`` uses ``"buf"``.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    BufferRef
        Handle with the allocated ``buffer_name`` the caller must use
        on follow-up calls.
    """
    server = _get_server(socket_name=socket_name)
    buffer_name = _allocate_buffer_name(logical_name)
    tmppath: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            tmppath = f.name
            f.write(content)
        argv = _tmux_argv(server, "load-buffer", "-b", buffer_name, tmppath)
        try:
            subprocess.run(argv, check=True, capture_output=True, timeout=5.0)
        except subprocess.TimeoutExpired as e:
            msg = f"load-buffer timeout after 5s for {buffer_name!r}"
            raise ToolError(msg) from e
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
            msg = f"load-buffer failed: {stderr or e}"
            raise ToolError(msg) from e
    finally:
        if tmppath is not None:
            pathlib.Path(tmppath).unlink(missing_ok=True)
    return BufferRef(buffer_name=buffer_name, logical_name=logical_name)


@handle_tool_errors
def paste_buffer(
    buffer_name: str,
    pane_id: str | None = None,
    bracket: bool = True,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> str:
    """Paste an MCP-owned buffer into a pane.

    Parameters
    ----------
    buffer_name : str
        Must match the full MCP-namespaced form returned by
        :func:`load_buffer`. Non-MCP buffers are rejected so the tool
        cannot be turned into an arbitrary-buffer reader.
    pane_id : str, optional
        Target pane ID.
    bracket : bool
        Use tmux bracketed paste mode. Default True.
    session_name, session_id, window_id : optional
        Pane resolution fallbacks.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message naming the target pane.
    """
    server = _get_server(socket_name=socket_name)
    cname = _validate_buffer_name(buffer_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )
    paste_args: list[str] = ["-b", cname]
    if bracket:
        paste_args.append("-p")
    paste_args.extend(["-t", pane.pane_id or ""])
    pane.cmd("paste-buffer", *paste_args)
    return f"Buffer {cname!r} pasted to pane {pane.pane_id}"


@handle_tool_errors
def show_buffer(
    buffer_name: str,
    max_lines: int | None = SHOW_BUFFER_DEFAULT_MAX_LINES,
    socket_name: str | None = None,
) -> BufferContent:
    """Read back the contents of an MCP-owned buffer.

    Output is tail-preserved: when the buffer exceeds ``max_lines`` the
    oldest lines are dropped and :attr:`BufferContent.content_truncated`
    is set so the caller can tell truncation happened and opt in to a
    full read via ``max_lines=None``. This mirrors ``capture_pane`` —
    one consistent bounded-output contract across read-heavy tools so
    a pathological ``load_buffer`` staging cannot blow the agent's
    context window on a single ``show_buffer`` call.

    Parameters
    ----------
    buffer_name : str
        Must match the full MCP-namespaced form.
    max_lines : int or None
        Maximum number of lines to return. Defaults to
        :data:`SHOW_BUFFER_DEFAULT_MAX_LINES`. Pass ``None`` for no
        truncation.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    BufferContent
        Structured result with ``buffer_name``, ``content``, and the
        truncation fields.
    """
    server = _get_server(socket_name=socket_name)
    cname = _validate_buffer_name(buffer_name)
    argv = _tmux_argv(server, "show-buffer", "-b", cname)
    try:
        completed = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired as e:
        msg = f"show-buffer timeout after 5s for {cname!r}"
        raise ToolError(msg) from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        msg = f"show-buffer failed for {cname!r}: {stderr or e}"
        raise ToolError(msg) from e
    raw = completed.stdout.decode(errors="replace")
    # Preserve a possible trailing newline so round-tripping through
    # load_buffer/show_buffer stays byte-identical when truncation
    # does not fire.
    lines = raw.splitlines()
    kept, truncated, dropped = _truncate_lines_tail(lines, max_lines)
    content = "\n".join(kept) if truncated else raw
    return BufferContent(
        buffer_name=cname,
        content=content,
        content_truncated=truncated,
        content_truncated_lines=dropped,
    )


@handle_tool_errors
def delete_buffer(
    buffer_name: str,
    socket_name: str | None = None,
) -> str:
    """Delete an MCP-owned buffer.

    Parameters
    ----------
    buffer_name : str
        Must match the full MCP-namespaced form.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    server = _get_server(socket_name=socket_name)
    cname = _validate_buffer_name(buffer_name)
    argv = _tmux_argv(server, "delete-buffer", "-b", cname)
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=5.0)
    except subprocess.TimeoutExpired as e:
        msg = f"delete-buffer timeout after 5s for {cname!r}"
        raise ToolError(msg) from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        msg = f"delete-buffer failed for {cname!r}: {stderr or e}"
        raise ToolError(msg) from e
    return f"Buffer {cname!r} deleted"


def register(mcp: FastMCP) -> None:
    """Register buffer tools with the MCP instance.

    ``load_buffer`` is tagged with :data:`ANNOTATIONS_SHELL` because its
    ``content`` argument is arbitrary user text that may carry
    interactive-environment side effects (commands about to be pasted
    into a shell). Other buffer tools are plain mutating ops on the
    tmux buffer store.
    """
    mcp.tool(title="Load Buffer", annotations=ANNOTATIONS_SHELL, tags={TAG_MUTATING})(
        load_buffer
    )
    mcp.tool(
        title="Paste Buffer",
        annotations=ANNOTATIONS_SHELL,
        tags={TAG_MUTATING},
    )(paste_buffer)
    mcp.tool(title="Show Buffer", annotations=ANNOTATIONS_RO, tags={TAG_READONLY})(
        show_buffer
    )
    mcp.tool(
        title="Delete Buffer",
        annotations=ANNOTATIONS_MUTATING,
        tags={TAG_MUTATING},
    )(delete_buffer)
