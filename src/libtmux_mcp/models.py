"""Pydantic models for MCP tool inputs and outputs."""

from __future__ import annotations

import enum
import typing as t

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    active_pane_id: str | None = Field(
        default=None,
        description="Pane id (``%N``) of the window's active pane.",
    )


class PaneInfo(BaseModel):
    """Serialized tmux pane."""

    pane_id: str = Field(description="Pane ID (e.g. '%1')")
    pane_index: str | None = Field(default=None, description="Pane index")
    pane_width: str | None = Field(default=None, description="Width in columns")
    pane_height: str | None = Field(default=None, description="Height in rows")
    pane_left: int | None = Field(
        default=None,
        description="Left edge column, 0-based and window-relative.",
    )
    pane_top: int | None = Field(
        default=None,
        description="Top edge row, 0-based and window-relative.",
    )
    pane_right: int | None = Field(
        default=None,
        description="Right edge column (inclusive), window-relative.",
    )
    pane_bottom: int | None = Field(
        default=None,
        description="Bottom edge row (inclusive), window-relative.",
    )
    pane_at_left: bool | None = Field(
        default=None,
        description="True when the pane touches the window's left edge.",
    )
    pane_at_right: bool | None = Field(
        default=None,
        description="True when the pane touches the window's right edge.",
    )
    pane_at_top: bool | None = Field(
        default=None,
        description=(
            "True when the pane touches the window's top edge. tmux "
            "accounts for ``pane-border-status`` here, so the top row "
            "may be 1 instead of 0 when the status bar is at the top."
        ),
    )
    pane_at_bottom: bool | None = Field(
        default=None,
        description="True when the pane touches the window's bottom edge.",
    )
    pane_tty: str | None = Field(
        default=None,
        description="TTY device path of the pane (e.g. '/dev/pts/5').",
    )
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
            "MCP caller identity for this pane. ``True`` when the pane "
            "matches the caller's ``TMUX_PANE`` *and* lives on the same "
            "tmux socket as the caller's ``TMUX`` (verified via socket "
            "realpath); ``False`` otherwise, including the case where "
            "the pane id matches but the socket does not or cannot be "
            "proven to; ``None`` when the MCP process is not running "
            "inside tmux at all."
        ),
    )


class PaneContentMatch(BaseModel):
    """A pane whose captured content matched a search pattern."""

    pane_id: str = Field(description="Pane ID (e.g. '%1')")
    pane_left: int | None = Field(
        default=None,
        description="Left edge column, 0-based and window-relative.",
    )
    pane_top: int | None = Field(
        default=None,
        description="Top edge row, 0-based and window-relative.",
    )
    pane_right: int | None = Field(
        default=None,
        description="Right edge column (inclusive), window-relative.",
    )
    pane_bottom: int | None = Field(
        default=None,
        description="Bottom edge row (inclusive), window-relative.",
    )
    pane_at_left: bool | None = Field(
        default=None,
        description="True when the pane touches the window's left edge.",
    )
    pane_at_right: bool | None = Field(
        default=None,
        description="True when the pane touches the window's right edge.",
    )
    pane_at_top: bool | None = Field(
        default=None,
        description=(
            "True when the pane touches the window's top edge. tmux "
            "accounts for ``pane-border-status`` here."
        ),
    )
    pane_at_bottom: bool | None = Field(
        default=None,
        description="True when the pane touches the window's bottom edge.",
    )
    pane_tty: str | None = Field(
        default=None,
        description="TTY device path of the pane (e.g. '/dev/pts/5').",
    )
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
            "MCP caller identity for this pane. ``True`` when the pane "
            "matches the caller's ``TMUX_PANE`` *and* lives on the same "
            "tmux socket as the caller's ``TMUX`` (verified via socket "
            "realpath); ``False`` otherwise, including the case where "
            "the pane id matches but the socket does not or cannot be "
            "proven to; ``None`` when the MCP process is not running "
            "inside tmux at all."
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
    risk_band_warned: bool = Field(
        default=False,
        description="Whether polling entered the history-limit trim-risk band",
    )


class CaptureSinceResult(BaseModel):
    """Incremental pane capture result with an opaque resume cursor."""

    pane_id: str = Field(description="Pane ID that was captured")
    cursor: str = Field(description="Opaque cursor to pass back to ``capture_since``")
    lines: list[str] = Field(
        default_factory=list,
        description="Captured lines, oldest first and tail-preserved if truncated",
    )
    elapsed_seconds: float = Field(description="Time spent capturing in seconds")
    lines_missed: bool = Field(
        default=False,
        description=(
            "True when prior history was no longer available, so ``lines`` "
            "is a conservative current visible capture rather than a complete delta"
        ),
    )
    truncated: bool = Field(
        default=False,
        description="True when ``lines`` was truncated by max_lines or max_bytes",
    )
    truncated_lines: int = Field(
        default=0,
        description="Number of lines dropped from the head when truncating",
    )
    truncated_bytes: int = Field(
        default=0,
        description="Approximate UTF-8 bytes dropped from the head when truncating",
    )


class RunCommandResult(BaseModel):
    """Result of running a shell command in a pane."""

    pane_id: str = Field(description="Pane ID that received the command")
    exit_status: int | None = Field(
        default=None,
        description="Shell exit status, or None when the command timed out",
    )
    timed_out: bool = Field(description="True when the wait timed out")
    elapsed_seconds: float = Field(description="Time spent waiting in seconds")
    output: list[str] = Field(
        default_factory=list,
        description="Tail-preserved pane output after the wait completes",
    )
    output_truncated: bool = Field(
        default=False,
        description="True when output was tail-preserved to stay within max_lines",
    )
    output_truncated_lines: int = Field(
        default=0,
        description="Number of pane lines dropped from the head when truncating",
    )


class SendKeysOperation(BaseModel):
    """One raw-input operation for batch sending.

    Used by :func:`~libtmux_mcp.tools.pane_tools.send_keys_batch`.
    """

    model_config = ConfigDict(extra="forbid")

    keys: str = Field(description="Keys or text to send.")
    pane_id: str | None = Field(
        default=None,
        description="Pane ID (e.g. '%1').",
    )
    session_name: str | None = Field(
        default=None,
        description="Session name for pane resolution.",
    )
    session_id: str | None = Field(
        default=None,
        description="Session ID (e.g. '$1') for pane resolution.",
    )
    window_id: str | None = Field(
        default=None,
        description="Window ID for pane resolution.",
    )
    enter: bool = Field(
        default=True,
        description="Whether to press Enter after sending keys.",
    )
    literal: bool = Field(
        default=False,
        description="Whether to send keys literally with no tmux key interpretation.",
    )
    suppress_history: bool = Field(
        default=False,
        description=(
            "Suppress shell history by prepending a space where the shell "
            "ignores space-prefixed commands."
        ),
    )


class SendKeysOperationResult(BaseModel):
    """Per-operation result from batch sending.

    Returned by :func:`~libtmux_mcp.tools.pane_tools.send_keys_batch`.
    """

    index: int = Field(description="Zero-based index in the submitted operation list.")
    pane_id: str | None = Field(
        default=None,
        description="Resolved pane ID, or None if target resolution failed.",
    )
    success: bool = Field(description="True when this operation sent successfully.")
    error: str | None = Field(
        default=None,
        description="Error message for this operation, if it failed.",
    )
    elapsed_seconds: float = Field(description="Time spent on this operation.")


class SendKeysBatchResult(BaseModel):
    """Structured result for a batch of raw-input send operations."""

    results: list[SendKeysOperationResult] = Field(
        default_factory=list,
        description="Per-operation results in attempted order.",
    )
    succeeded: int = Field(description="Number of operations sent successfully.")
    failed: int = Field(description="Number of operations that failed.")
    stopped_at: int | None = Field(
        default=None,
        description=(
            "Index where processing stopped because on_error='stop', or None "
            "when all operations were attempted."
        ),
    )


class ToolCallOperation(BaseModel):
    """One nested MCP tool call for a batch wrapper."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(description="Registered MCP tool name to call.")
    arguments: dict[str, t.Any] = Field(
        default_factory=dict,
        description="Arguments for the nested tool call.",
    )


class ToolCallOperationResult(BaseModel):
    """Per-operation result from a generic MCP tool batch."""

    index: int = Field(description="Zero-based index in the submitted operation list.")
    tool: str = Field(description="Nested tool name that was attempted.")
    success: bool = Field(description="True when this nested tool call succeeded.")
    error: str | None = Field(
        default=None,
        description="Error message for this operation, if it failed.",
    )
    content: list[dict[str, t.Any]] = Field(
        default_factory=list,
        description="MCP content blocks returned by the nested tool.",
    )
    structured_content: dict[str, t.Any] | None = Field(
        default=None,
        description="Structured content returned by the nested tool, if any.",
    )
    meta: dict[str, t.Any] | None = Field(
        default=None,
        description="Runtime metadata returned by the nested tool, if any.",
    )
    elapsed_seconds: float = Field(description="Time spent on this operation.")


class ToolCallBatchResult(BaseModel):
    """Structured result for a serial batch of MCP tool calls."""

    results: list[ToolCallOperationResult] = Field(
        default_factory=list,
        description="Per-operation results in attempted order.",
    )
    succeeded: int = Field(description="Number of nested tool calls that succeeded.")
    failed: int = Field(description="Number of nested tool calls that failed.")
    stopped_at: int | None = Field(
        default=None,
        description=(
            "Index where processing stopped because on_error='stop', or None "
            "when all operations were attempted."
        ),
    )
    response_truncated: bool = Field(
        default=False,
        description=(
            "True when nested result payloads were elided to keep the batch "
            "response under the server response cap."
        ),
    )
    response_truncated_bytes: int = Field(
        default=0,
        description="Approximate serialized bytes removed from nested result payloads.",
    )


class PaneSnapshot(BaseModel):
    """Rich screen capture with metadata: content, cursor, mode, and scroll state."""

    pane_id: str = Field(description="Pane ID (e.g. '%1')")
    content: str = Field(description="Visible pane text")
    cursor_x: int = Field(description="Cursor column (0-based)")
    cursor_y: int = Field(description="Cursor row (0-based)")
    pane_width: int = Field(description="Pane width in columns")
    pane_height: int = Field(description="Pane height in rows")
    pane_left: int | None = Field(
        default=None,
        description="Left edge column, 0-based and window-relative.",
    )
    pane_top: int | None = Field(
        default=None,
        description="Top edge row, 0-based and window-relative.",
    )
    pane_right: int | None = Field(
        default=None,
        description="Right edge column (inclusive), window-relative.",
    )
    pane_bottom: int | None = Field(
        default=None,
        description="Bottom edge row (inclusive), window-relative.",
    )
    pane_at_left: bool | None = Field(
        default=None,
        description="True when the pane touches the window's left edge.",
    )
    pane_at_right: bool | None = Field(
        default=None,
        description="True when the pane touches the window's right edge.",
    )
    pane_at_top: bool | None = Field(
        default=None,
        description=(
            "True when the pane touches the window's top edge. tmux "
            "accounts for ``pane-border-status`` here."
        ),
    )
    pane_at_bottom: bool | None = Field(
        default=None,
        description="True when the pane touches the window's bottom edge.",
    )
    pane_tty: str | None = Field(
        default=None,
        description="TTY device path of the pane (e.g. '/dev/pts/5').",
    )
    pane_pid: str | None = Field(default=None, description="Process ID")
    pane_dead: bool | None = Field(
        default=None,
        description="True when tmux reports the pane process has exited.",
    )
    alternate_on: bool | None = Field(
        default=None,
        description="True when the pane is using the alternate screen.",
    )
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
        description=(
            "MCP caller identity for this pane. ``True`` when the pane "
            "matches the caller's ``TMUX_PANE`` *and* lives on the same "
            "tmux socket as the caller's ``TMUX`` (verified via socket "
            "realpath); ``False`` otherwise, including the case where "
            "the pane id matches but the socket does not or cannot be "
            "proven to; ``None`` when the MCP process is not running "
            "inside tmux at all."
        ),
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
    """Paginated result of :func:`~libtmux_mcp.tools.pane_tools.search_panes`.

    Wrapping the match list lets us surface bounded-output information
    that a bare ``list`` of
    :class:`~libtmux_mcp.models.PaneContentMatch` rows cannot: whether
    pagination truncated the result set, which panes were skipped, and
    the ``offset``/``limit`` that produced this page. Agents can
    re-request with a higher ``offset`` to retrieve subsequent pages.
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
    commands registered at sparse indices.
    :class:`~libtmux_mcp.models.HookEntry` flattens one index+command
    pair into a serialisable row.
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
    """Structured result for hook introspection.

    Returned by :func:`~libtmux_mcp.tools.hook_tools.show_hooks` and
    :func:`~libtmux_mcp.tools.hook_tools.show_hook`.
    Flat list of :class:`~libtmux_mcp.models.HookEntry` instances so
    MCP clients can iterate without caring whether the underlying tmux
    hook is scalar or array-shaped.
    """

    entries: list[HookEntry] = Field(default_factory=list)


class BufferRef(BaseModel):
    """Handle returned by :func:`~libtmux_mcp.tools.buffer_tools.load_buffer`.

    Agent-created tmux paste buffers are namespaced with a per-call UUID
    to avoid collisions on the server-global buffer namespace when
    concurrent agents (or parallel tool calls from a single agent) are
    staging content. Callers must use the ``buffer_name`` this model
    carries on subsequent
    :func:`~libtmux_mcp.tools.buffer_tools.paste_buffer`,
    :func:`~libtmux_mcp.tools.buffer_tools.show_buffer`, and
    :func:`~libtmux_mcp.tools.buffer_tools.delete_buffer` calls.
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
    """Structured result of :func:`~libtmux_mcp.tools.buffer_tools.show_buffer`."""

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


class TmuxOperationStatus(str, enum.Enum):
    """Execution status for one typed tmux operation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    PLANNED = "planned"


class PaneIdTarget(BaseModel):
    """Target a concrete pane by its tmux ID."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["pane_id"] = Field(
        default="pane_id",
        description="Target discriminator.",
    )
    pane_id: str = Field(description="Concrete tmux pane ID, e.g. '%1'.")


class RefTarget(BaseModel):
    """Target a pane created earlier in the same operation list."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["ref"] = Field(
        default="ref",
        description="Target discriminator.",
    )
    ref: str = Field(
        description="Reference name captured from an earlier split_pane operation.",
    )


PaneTarget: t.TypeAlias = t.Annotated[
    PaneIdTarget | RefTarget,
    Field(discriminator="kind"),
]


class SplitPaneOperation(BaseModel):
    """Split a pane and optionally expose the new pane under ``ref``."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["split_pane"] = Field(
        default="split_pane",
        description="Operation discriminator.",
    )
    target: PaneTarget = Field(description="Pane to split.")
    ref: str | None = Field(
        default=None,
        description="Reference name for the created pane ID.",
    )
    horizontal: bool = Field(
        default=False,
        description="Split left/right (-h) instead of top/bottom.",
    )
    shell: str | None = Field(
        default=None,
        description="Command to run in the new pane instead of the default shell.",
    )


class TmuxSendKeysOperation(BaseModel):
    """Send keys to a pane target."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["send_keys"] = Field(
        default="send_keys",
        description="Operation discriminator.",
    )
    target: PaneTarget = Field(description="Pane to send keys to.")
    keys: str = Field(description="Keys or text to send.")
    enter: bool = Field(default=True, description="Press Enter after sending keys.")
    literal: bool = Field(
        default=False,
        description="Pass -l so tmux sends keys literally.",
    )


class ResizePaneOperation(BaseModel):
    """Resize a pane by dimensions or zoom toggle."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["resize_pane"] = Field(
        default="resize_pane",
        description="Operation discriminator.",
    )
    target: PaneTarget = Field(description="Pane to resize.")
    height: int | None = Field(default=None, description="New height in lines.")
    width: int | None = Field(default=None, description="New width in columns.")
    zoom: bool | None = Field(default=None, description="Toggle pane zoom.")

    @model_validator(mode="after")
    def _validate_resize(self) -> ResizePaneOperation:
        if self.zoom is not None and (
            self.height is not None or self.width is not None
        ):
            msg = "Cannot combine zoom with height/width."
            raise ValueError(msg)
        if self.zoom is None and self.height is None and self.width is None:
            msg = "Provide height, width, or zoom."
            raise ValueError(msg)
        return self


class SelectLayoutOperation(BaseModel):
    """Select a layout for a tmux window."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["select_layout"] = Field(
        default="select_layout",
        description="Operation discriminator.",
    )
    window_id: str = Field(description="Concrete tmux window ID, e.g. '@1'.")
    layout: str = Field(description="Layout name or custom layout string.")


class SetOptionOperation(BaseModel):
    """Set a tmux option at server, session, window, or pane scope."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["set_option"] = Field(
        default="set_option",
        description="Operation discriminator.",
    )
    option: str = Field(description="Option name to set.")
    value: str = Field(description="Option value.")
    scope: t.Literal["server", "session", "window", "pane"] | None = Field(
        default=None,
        description="Option scope; omitted means server option.",
    )
    target: str | None = Field(
        default=None,
        description="Target identifier for session, window, or pane scoped options.",
    )
    global_: bool = Field(default=False, description="Set the global option table.")

    @model_validator(mode="after")
    def _validate_target(self) -> SetOptionOperation:
        if self.target is not None and self.scope is None:
            msg = "scope is required when target is specified."
            raise ValueError(msg)
        if self.scope in {"session", "window", "pane"} and self.target is None:
            msg = "target is required when scope is 'session', 'window', or 'pane'."
            raise ValueError(msg)
        return self


class CapturePaneOperation(BaseModel):
    """Capture pane output as a standalone read operation."""

    model_config = ConfigDict(extra="forbid")

    kind: t.Literal["capture_pane"] = Field(
        default="capture_pane",
        description="Operation discriminator.",
    )
    target: PaneTarget = Field(description="Pane to capture.")
    start: int | None = Field(default=None, description="Start capture line.")
    end: int | None = Field(default=None, description="End capture line.")


TmuxOperation: t.TypeAlias = t.Annotated[
    SplitPaneOperation
    | TmuxSendKeysOperation
    | ResizePaneOperation
    | SelectLayoutOperation
    | SetOptionOperation
    | CapturePaneOperation,
    Field(discriminator="kind"),
]


class SplitPaneStepResult(BaseModel):
    """Result for one ``split_pane`` operation."""

    kind: t.Literal["split_pane"] = Field(
        default="split_pane",
        description="Operation kind discriminator.",
    )
    index: int = Field(description="Zero-based operation index.")
    status: TmuxOperationStatus = Field(description="Execution status.")
    pane_id: str | None = Field(
        default=None,
        description="Concrete pane ID created by a ref-producing split, if any.",
    )
    error: str | None = Field(
        default=None,
        description="Failure message when the operation failed.",
    )


class CapturePaneStepResult(BaseModel):
    """Result for one ``capture_pane`` operation."""

    kind: t.Literal["capture_pane"] = Field(
        default="capture_pane",
        description="Operation kind discriminator.",
    )
    index: int = Field(description="Zero-based operation index.")
    status: TmuxOperationStatus = Field(description="Execution status.")
    lines: list[str] | None = Field(
        default=None,
        description="Captured pane lines on success.",
    )
    error: str | None = Field(
        default=None,
        description="Failure message when the operation failed.",
    )


class OperationStepResult(BaseModel):
    """Result for an operation that returns status only."""

    kind: t.Literal["send_keys", "resize_pane", "select_layout", "set_option"] = Field(
        description="Operation kind discriminator.",
    )
    index: int = Field(description="Zero-based operation index.")
    status: TmuxOperationStatus = Field(description="Execution status.")
    error: str | None = Field(
        default=None,
        description="Failure message when the operation failed.",
    )


TmuxStepResult: t.TypeAlias = t.Annotated[
    SplitPaneStepResult | CapturePaneStepResult | OperationStepResult,
    Field(discriminator="kind"),
]


class TmuxOperationDispatchResult(BaseModel):
    """Diagnostics for one native tmux dispatch."""

    index: int = Field(description="Operation index this dispatch ran.")
    argv: list[str] = Field(description="Rendered tmux argv.")
    returncode: int | None = Field(description="tmux process exit code, if run.")
    stdout: list[str] = Field(default_factory=list, description="stdout lines.")
    stderr: list[str] = Field(default_factory=list, description="stderr lines.")


class RunTmuxDiagnostics(BaseModel):
    """Dispatch diagnostics returned only when ``explain`` is set."""

    dispatch_count: int = Field(description="Number of native tmux dispatches.")
    dispatches: list[TmuxOperationDispatchResult] = Field(
        description="Native tmux dispatches used to run the operations.",
    )


class RunTmuxOperationsResult(BaseModel):
    """Result of running typed tmux operations."""

    succeeded: bool = Field(description="False when any operation failed or skipped.")
    dry_run: bool = Field(
        default=False,
        description="True when dispatches were planned but not executed.",
    )
    steps: list[TmuxStepResult] = Field(
        description="Per-operation results in input order.",
    )
    created_panes: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of split_pane ref names to concrete pane IDs.",
    )
    rolled_back_panes: list[str] = Field(
        default_factory=list,
        description="Pane IDs killed by rollback_on_error.",
    )
    rollback_errors: list[str] = Field(
        default_factory=list,
        description="Errors raised while rolling back created panes.",
    )
    diagnostics: RunTmuxDiagnostics | None = Field(
        default=None,
        description="Dispatch diagnostics, present only when explain is set.",
    )
