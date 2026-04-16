"""Pydantic models for MCP tool inputs and outputs."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field


class SessionInfo(BaseModel):
    """Serialized tmux session."""

    session_id: str = Field(description="Session ID (e.g. '$1')")
    session_name: str | None = Field(default=None, description="Session name")
    window_count: int = Field(description="Number of windows")
    session_attached: str | None = Field(
        default=None, description="Attached client count"
    )
    session_created: str | None = Field(default=None, description="Creation timestamp")
    active_pane_id: str | None = Field(
        default=None,
        description=(
            "Pane id (``%N``) of the session's active pane. Guaranteed "
            "non-None on ``create_session`` return (libtmux creates the "
            "session with one initial pane). May be None from "
            "``list_sessions`` rows for sessions in transient teardown "
            "states where ``active_pane`` is unavailable."
        ),
    )


class WindowInfo(BaseModel):
    """Serialized tmux window."""

    window_id: str = Field(description="Window ID (e.g. '@1')")
    window_name: str | None = Field(default=None, description="Window name")
    window_index: str | None = Field(default=None, description="Window index")
    session_id: str | None = Field(default=None, description="Parent session ID")
    session_name: str | None = Field(default=None, description="Parent session name")
    pane_count: int = Field(description="Number of panes")
    window_layout: str | None = Field(default=None, description="Layout string")
    window_active: str | None = Field(
        default=None, description="Active flag ('1' or '0')"
    )
    window_width: str | None = Field(default=None, description="Width in columns")
    window_height: str | None = Field(default=None, description="Height in rows")


class PaneInfo(BaseModel):
    """Serialized tmux pane."""

    pane_id: str = Field(description="Pane ID (e.g. '%1')")
    pane_index: str | None = Field(default=None, description="Pane index")
    pane_width: str | None = Field(default=None, description="Width in columns")
    pane_height: str | None = Field(default=None, description="Height in rows")
    pane_current_command: str | None = Field(
        default=None, description="Running command"
    )
    pane_current_path: str | None = Field(
        default=None, description="Current working directory"
    )
    pane_pid: str | None = Field(default=None, description="Process ID")
    pane_title: str | None = Field(default=None, description="Pane title")
    pane_active: str | None = Field(
        default=None, description="Active flag ('1' or '0')"
    )
    window_id: str | None = Field(default=None, description="Parent window ID")
    session_id: str | None = Field(default=None, description="Parent session ID")
    is_caller: bool | None = Field(
        default=None,
        description=(
            "True if this pane is the MCP caller's own pane "
            "(detected via TMUX_PANE env var)"
        ),
    )


class PaneContentMatch(BaseModel):
    """A pane whose captured content matched a search pattern."""

    pane_id: str = Field(description="Pane ID (e.g. '%1')")
    pane_current_command: str | None = Field(
        default=None, description="Running command"
    )
    pane_current_path: str | None = Field(
        default=None, description="Current working directory"
    )
    window_id: str | None = Field(default=None, description="Parent window ID")
    window_name: str | None = Field(default=None, description="Parent window name")
    session_id: str | None = Field(default=None, description="Parent session ID")
    session_name: str | None = Field(default=None, description="Parent session name")
    matched_lines: list[str] = Field(description="Lines containing the match")
    is_caller: bool | None = Field(
        default=None,
        description=(
            "True if this pane is the MCP caller's own pane "
            "(detected via TMUX_PANE env var)"
        ),
    )


class ServerInfo(BaseModel):
    """Serialized tmux server info."""

    is_alive: bool = Field(description="Whether the server is running")
    socket_name: str | None = Field(default=None, description="Socket name")
    socket_path: str | None = Field(default=None, description="Socket path")
    session_count: int = Field(description="Number of sessions")
    version: str | None = Field(default=None, description="tmux version")


class OptionResult(BaseModel):
    """Result of a show_option call."""

    option: str = Field(description="Option name")
    value: t.Any = Field(description="Option value")


class OptionSetResult(BaseModel):
    """Result of a set_option call."""

    option: str = Field(description="Option name")
    value: str = Field(description="Value that was set")
    status: str = Field(description="Operation status")


class EnvironmentResult(BaseModel):
    """Result of a show_environment call."""

    variables: dict[str, str | bool] = Field(description="Environment variable mapping")


class EnvironmentSetResult(BaseModel):
    """Result of a set_environment call."""

    name: str = Field(description="Variable name")
    value: str = Field(description="Value that was set")
    status: str = Field(description="Operation status")


class WaitForTextResult(BaseModel):
    """Result of waiting for text to appear in a pane."""

    found: bool = Field(description="Whether the pattern was found before timeout")
    matched_lines: list[str] = Field(
        default_factory=list,
        description="Lines matching the pattern (empty if not found)",
    )
    pane_id: str = Field(description="Pane ID that was polled")
    elapsed_seconds: float = Field(description="Time spent waiting in seconds")
    timed_out: bool = Field(description="Whether the timeout was reached")


class PaneSnapshot(BaseModel):
    """Rich screen capture with metadata: content, cursor, mode, and scroll state."""

    pane_id: str = Field(description="Pane ID (e.g. '%1')")
    content: str = Field(description="Visible pane text")
    cursor_x: int = Field(description="Cursor column (0-based)")
    cursor_y: int = Field(description="Cursor row (0-based)")
    pane_width: int = Field(description="Pane width in columns")
    pane_height: int = Field(description="Pane height in rows")
    pane_in_mode: bool = Field(description="True if pane is in copy-mode or view-mode")
    pane_mode: str | None = Field(
        default=None, description="Mode name (e.g. 'copy-mode') or None if normal"
    )
    scroll_position: int | None = Field(
        default=None,
        description="Lines scrolled back in copy mode (None if not in copy mode)",
    )
    history_size: int = Field(description="Total scrollback lines available")
    title: str | None = Field(default=None, description="Pane title")
    pane_current_command: str | None = Field(
        default=None, description="Running command"
    )
    pane_current_path: str | None = Field(
        default=None, description="Current working directory"
    )
    is_caller: bool | None = Field(
        default=None,
        description="True if this is the MCP caller's own pane",
    )
    content_truncated: bool = Field(
        default=False,
        description=(
            "True if ``content`` was tail-preserved to stay within "
            "``max_lines``; oldest lines were dropped."
        ),
    )
    content_truncated_lines: int = Field(
        default=0,
        description="Number of lines dropped from the head when truncating.",
    )


class SearchPanesResult(BaseModel):
    """Paginated result of :func:`search_panes`.

    Wrapping the match list lets us surface bounded-output information
    that a bare ``list[PaneContentMatch]`` cannot: whether pagination
    truncated the result set, which panes were skipped, and the
    ``offset``/``limit`` that produced this page. Agents can re-request
    with a higher ``offset`` to retrieve subsequent pages.
    """

    matches: list[PaneContentMatch] = Field(
        default_factory=list,
        description="PaneContentMatch entries for this page.",
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True when the result set was truncated by ``limit`` or "
            "by ``max_matched_lines_per_pane`` on any pane."
        ),
    )
    truncated_panes: list[str] = Field(
        default_factory=list,
        description=(
            "Pane IDs that matched but were skipped because the global "
            "``limit`` was already satisfied. Re-request with a larger "
            "``offset`` to retrieve them."
        ),
    )
    total_panes_matched: int = Field(
        description=(
            "Total number of panes that matched the pattern before "
            "``offset`` / ``limit`` were applied."
        ),
    )
    offset: int = Field(description="The ``offset`` that produced this page.")
    limit: int | None = Field(description="The ``limit`` that produced this page.")


class HookEntry(BaseModel):
    """One entry in a tmux hook array.

    Hooks like ``session-renamed`` are arrays — they can have multiple
    commands registered at sparse indices. :class:`HookEntry` flattens
    one index+command pair into a serialisable row.
    """

    hook_name: str = Field(description="Hook name (e.g. 'pane-exited').")
    index: int | None = Field(
        default=None,
        description=(
            "Array index for array-style hooks (e.g. session-renamed[3]). "
            "``None`` for scalar hooks."
        ),
    )
    command: str = Field(description="tmux command string registered at that index.")


class HookListResult(BaseModel):
    """Structured result of :func:`show_hooks` / :func:`show_hook`.

    Flat list of :class:`HookEntry` instances so MCP clients can iterate
    without caring whether the underlying tmux hook is scalar or array-
    shaped.
    """

    entries: list[HookEntry] = Field(default_factory=list)


class BufferRef(BaseModel):
    """Handle returned by :func:`load_buffer` for later buffer operations.

    Agent-created tmux paste buffers are namespaced with a per-call UUID
    to avoid collisions on the server-global buffer namespace when
    concurrent agents (or parallel tool calls from a single agent) are
    staging content. Callers must use the ``buffer_name`` this model
    carries on subsequent ``paste_buffer`` / ``show_buffer`` /
    ``delete_buffer`` calls.
    """

    buffer_name: str = Field(
        description=(
            "The actual tmux buffer name (with prefix and UUID nonce). "
            "Pass this back to paste_buffer/show_buffer/delete_buffer."
        ),
    )
    logical_name: str | None = Field(
        default=None,
        description="Optional logical name supplied by the caller, if any.",
    )


class BufferContent(BaseModel):
    """Structured result of :func:`show_buffer`."""

    buffer_name: str = Field(description="Agent-namespaced buffer name.")
    content: str = Field(description="Buffer contents as text.")
    content_truncated: bool = Field(
        default=False,
        description=(
            "True if ``content`` was tail-preserved to stay within "
            "``max_lines``; oldest lines were dropped."
        ),
    )
    content_truncated_lines: int = Field(
        default=0,
        description="Number of lines dropped from the head when truncating.",
    )


class ContentChangeResult(BaseModel):
    """Result of waiting for any screen content change."""

    changed: bool = Field(description="Whether the content changed before timeout")
    pane_id: str = Field(description="Pane ID that was polled")
    elapsed_seconds: float = Field(description="Time spent waiting in seconds")
    timed_out: bool = Field(description="Whether the timeout was reached")
