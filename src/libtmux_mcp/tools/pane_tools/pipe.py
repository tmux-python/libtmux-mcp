"""Pipe-pane tool for streaming pane output to a file."""

from __future__ import annotations

import shlex

from fastmcp.exceptions import ToolError

from libtmux_mcp._utils import (
    _get_server,
    _resolve_pane,
    handle_tool_errors,
)


@handle_tool_errors
def pipe_pane(
    pane_id: str | None = None,
    output_path: str | None = None,
    append: bool = True,
    session_name: str | None = None,
    session_id: str | None = None,
    window_id: str | None = None,
    socket_name: str | None = None,
) -> str:
    """Start or stop piping pane output to a file.

    When output_path is given, starts logging all pane output to the file.
    When output_path is None, stops any active pipe for the pane.

    .. warning::
       This tool writes to arbitrary filesystem paths chosen by the MCP
       client. There is no allow-list; the server will create files
       anywhere the server process has write access. Treat this as
       elevated-risk even though it sits in the ``mutating`` safety
       tier — it is the broadest-reach tool in that tier. If you run
       libtmux-mcp on untrusted input, consider
       ``LIBTMUX_SAFETY=readonly`` or run the server under a user with
       a scoped home directory. See :doc:`/topics/safety` for the full
       footgun list.

    Parameters
    ----------
    pane_id : str, optional
        Pane ID (e.g. '%1').
    output_path : str, optional
        File path to write output to. None stops piping.
    append : bool
        Whether to append to the file. Default True. If False, overwrites.
    session_name : str, optional
        Session name for pane resolution.
    session_id : str, optional
        Session ID for pane resolution.
    window_id : str, optional
        Window ID for pane resolution.
    socket_name : str, optional
        tmux socket name.

    Returns
    -------
    str
        Confirmation message.
    """
    server = _get_server(socket_name=socket_name)
    pane = _resolve_pane(
        server,
        pane_id=pane_id,
        session_name=session_name,
        session_id=session_id,
        window_id=window_id,
    )

    if output_path is None:
        pane.cmd("pipe-pane")
        return f"Piping stopped for pane {pane.pane_id}"

    if not output_path.strip():
        msg = "output_path must be a non-empty path, or None to stop piping."
        raise ToolError(msg)

    redirect = ">>" if append else ">"
    pane.cmd("pipe-pane", f"cat {redirect} {shlex.quote(output_path)}")
    return f"Piping pane {pane.pane_id} to {output_path}"
