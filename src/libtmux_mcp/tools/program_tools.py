"""Owned terminal programs and their cleanup records."""

from __future__ import annotations

import math
import re
import threading
import typing as t

from libtmux import Session
from pydantic import Field

from libtmux_mcp._programs import _program_runs
from libtmux_mcp._utils import (
    ANNOTATIONS_AMBIENT_UNKNOWN,
    TAG_SELF_BOUNDED,
    TOOLSET_EXECUTE,
    TOOLSET_INSPECT,
    ExpectedToolError,
    _coerce_dict_arg,
    _new_server,
    _prepare_start_directory,
    handle_tool_errors_async,
)
from libtmux_mcp._wait_policy import _wait_ceiling_seconds
from libtmux_mcp.models import (
    ProgramCleanupResult,
    ProgramRunInfo,
    ProgramRunPage,
    ProgramRunResult,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP


@handle_tool_errors_async
async def run_program(
    session_id: t.Annotated[str, Field(pattern=r"^\$[0-9]+$")],
    argv: t.Annotated[list[str], Field(min_length=2, max_length=256)],
    socket_name: str | None = None,
    socket_path: str | None = None,
    start_directory: str | None = None,
    environment: dict[str, str] | str | None = None,
    width: int | None = None,
    height: int | None = None,
    timeout: float = 30,
    output_bytes: t.Annotated[int, Field(ge=1, le=65536)] = 8192,
) -> ProgramRunResult:
    """Run literal argv in a fresh PTY, return its exit and remove its pane.

    Discover a stable session_id with list_sessions first. Requires connected
    tmux 3.8+; get_server_capabilities explains availability. Arguments bypass
    a command shell. The returned tail is rendered terminal text with merged
    stdout/stderr, not a lossless byte stream. Request cancellation and timeout
    clean up the owned pane; pane removal does not prove descendant exit.
    Use list_program_runs and cleanup_program if cleanup remains unresolved.
    Terminal ownership does not confine filesystem, network or privileges.

    Parameters
    ----------
    session_id : str
        Stable borrowed session ID such as ``$0``; its existing panes survive.
    argv : list[str]
        Executable followed by at least one argument, without shell expansion.
    socket_name : str, optional
        Socket name; defaults to LIBTMUX_SOCKET.
    socket_path : str, optional
        Socket path; defaults to LIBTMUX_SOCKET_PATH and takes precedence.
    start_directory : str, optional
        Existing directory for the program; relative paths resolve locally.
    environment : dict or str, optional
        Program environment overrides, also accepted as a JSON object string.
        Values are visible in native argv and tmux state; use credential references.
    width : int, optional
        Initial terminal width, independent of the borrowed window.
    height : int, optional
        Initial terminal height, independent of the borrowed window.
    timeout : float
        Positive operation budget, clamped to LIBTMUX_MCP_WAIT_MAX_SECONDS.
        Bounded cleanup can continue after that budget expires.
    output_bytes : int
        UTF-8 byte limit for the returned tail, from 1 to 65536.

    Returns
    -------
    ProgramRunResult
        Exact status or signal and a bounded screen after verified pane cleanup.
    """
    if re.fullmatch(r"\$[0-9]+", session_id) is None:
        msg = "session_id must be a stable ID from list_sessions"
        raise ExpectedToolError(msg)
    if not math.isfinite(timeout) or timeout <= 0:
        msg = "timeout must be finite and positive"
        raise ExpectedToolError(msg)
    if type(output_bytes) is not int or not 1 <= output_bytes <= 65536:
        msg = "output_bytes must be an integer from 1 to 65536"
        raise ExpectedToolError(msg)
    effective_timeout = min(timeout, _wait_ceiling_seconds())
    server = _new_server(socket_name, socket_path)

    def work(cancel: threading.Event) -> ProgramRunResult:
        session = Session(server=server, session_id=session_id)
        result = session.run_program(
            argv,
            timeout=effective_timeout,
            cancel_event=cancel,
            start_directory=_prepare_start_directory(start_directory),
            environment=_coerce_dict_arg("environment", environment),
            width=width,
            height=height,
        )
        raw = result.screen.text.encode("utf-8")
        return ProgramRunResult(
            pane_id=result.exit.pane_id,
            exit_status=result.exit.exit_status,
            exit_signal=result.exit.exit_signal,
            screen_text=raw[-output_bytes:].decode("utf-8", "ignore"),
            screen_truncated=len(raw) > output_bytes,
            effective_timeout=effective_timeout,
        )

    return await _program_runs.execute(work, "run", server, session_id)


@handle_tool_errors_async
async def list_program_runs(
    cursor: t.Annotated[str | None, Field(pattern=r"^[0-9]{1,20}$")] = None,
    limit: t.Annotated[int, Field(ge=1, le=16)] = 4,
) -> ProgramRunPage:
    """Page through running workers and unresolved cleanup in this MCP process.

    Summaries include a 256-byte error preview. Use get_program_run for one
    owner's cleanup errors and prior output. Records remain until cleanup
    succeeds; they do not survive a process restart. At most 32 queued,
    running and unresolved owners share capacity; full capacity refuses new runs.
    A missing pane ID can mean an unverified receipt, not absence of effects.
    Pages follow creation order. Cleanup between pages cannot invalidate the
    cursor; new records can appear later. No page grants cleanup authority.

    Parameters
    ----------
    cursor : str, optional
        Copy next_cursor from the previous page; omit to start again.
    limit : int
        Maximum summaries per page, from 1 to 16; defaults to 4.

    Returns
    -------
    ProgramRunPage
        Local summaries and next_cursor, which is null at the current end.
    """
    if type(limit) is not int or not 1 <= limit <= 16:
        msg = "limit must be an integer from 1 to 16"
        raise ExpectedToolError(msg)
    if cursor is not None and re.fullmatch(r"[0-9]{1,20}", cursor) is None:
        msg = "cursor must be copied from list_program_runs"
        raise ExpectedToolError(msg)
    return ProgramRunPage(**_program_runs.page(cursor, limit))


@handle_tool_errors_async
async def get_program_run(run_id: str) -> ProgramRunInfo:
    """Inspect one retained program's cleanup evidence without querying tmux.

    Start with list_program_runs or program_recovery_id from error metadata.
    This returns up to eight cleanup error summaries of 2048 UTF-8 bytes each,
    the bounded primary error, and any prior exit or 2048-byte capture tail.
    A running or cleanup_in_progress record is still settling. Retry
    cleanup_program only for cleanup_required. Missing or removed records
    return an error; no new owner is adopted.

    Parameters
    ----------
    run_id : str
        Opaque owner ID from this MCP process, not a pane ID or page cursor.

    Returns
    -------
    ProgramRunInfo
        Current local disposition and bounded prior evidence for one owner.
    """
    return ProgramRunInfo(**_program_runs.inspect(run_id))


@handle_tool_errors_async
async def cleanup_program(run_id: str) -> ProgramCleanupResult:
    """Retry cleanup of the original owner retained by a failed program call.

    Use a cleanup_required run_id from list_program_runs or error metadata.
    This can terminate that owner's program. It cannot adopt arbitrary panes
    or bypass a missing creation receipt or changed lifetime. Failed cleanup
    keeps the record available for another retry. The borrowed session survives.

    Parameters
    ----------
    run_id : str
        Opaque record ID from this MCP process, not a pane ID.

    Returns
    -------
    ProgramCleanupResult
        Verified cleanup; native client-only failures may have no pane ID.
    """
    pane_id = await _program_runs.cleanup(run_id)
    return ProgramCleanupResult(run_id=run_id, pane_id=pane_id)


def register(mcp: FastMCP) -> None:
    """Register owned execution, recovery and daemon discovery."""
    for tool, title, toolset in (
        (run_program, "Run Program", TOOLSET_EXECUTE),
        (cleanup_program, "Clean Up Program", TOOLSET_EXECUTE),
    ):
        mcp.tool(
            title=title,
            annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
            tags={toolset, TAG_SELF_BOUNDED},
        )(tool)
    for inspect_tool, title in (
        (list_program_runs, "List Program Runs"),
        (get_program_run, "Get Program Run"),
    ):
        mcp.tool(
            title=title,
            annotations=ANNOTATIONS_AMBIENT_UNKNOWN,
            tags={TOOLSET_INSPECT},
        )(inspect_tool)
