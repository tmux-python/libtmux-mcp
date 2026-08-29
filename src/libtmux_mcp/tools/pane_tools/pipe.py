"""Pipe-pane tool for streaming pane output to a file."""

from __future__ import annotations

import pathlib
import shlex

from libtmux_mcp._errors import ExpectedToolError, handle_tool_errors
from libtmux_mcp._resolve import _resolve_pane
from libtmux_mcp._servers import _get_server
from libtmux_mcp._tmux_format import escape_format_time


def _raise_if_unwritable(output_path: str) -> None:
    """Refuse a destination the redirect cannot possibly write.

    tmux hands the pipe command to a shell and reports success whatever
    that shell then does, so a redirect into a missing directory or an
    unwritable path fails silently and the caller is told its pane is
    being captured to a file that will never appear. Worse, a stale file
    already at that path then reads back as if it were live capture.

    Checked BEFORE piping rather than after. ``#{pane_pipe}`` reads
    ``1`` immediately after a doomed pipe, because the shell has been
    spawned and has not yet failed on the redirect; only a later poll
    sees ``0``. Polling it would work, but the answer is available
    synchronously and a poll is latency spent on every call.

    Does what the redirect does rather than predicting it. A stat-based
    predicate is a proxy for "a shell can append here", and each new
    stat check is another proxy: measured, an existing DIRECTORY, a
    DANGLING SYMLINK into an unwritable directory, ``/dev/full`` and a
    300-character basename all passed a parent-directory-plus-``access``
    check and captured nothing. Opening the path for append answers the
    real question and closes all four at once -- the directory raises
    ``IsADirectoryError``, the dangling link resolves and fails on the
    real parent, ``/dev/full`` is not a regular file, and the long name
    raises ``ENAMETOOLONG``.

    The regular-file test is separate because ``open`` succeeds on a
    FIFO with a reader and on a character device: a reader-less FIFO
    blocks the shell in ``open()`` forever, so it looks healthy and
    captures nothing, and no poll of any duration can see that.
    """
    target = pathlib.Path(output_path)
    if target.exists() and not target.is_file():
        kind = "a directory" if target.is_dir() else "not a regular file"
        msg = (
            f"cannot pipe to {output_path!r}: it is {kind}. tmux would "
            "report success and capture nothing."
        )
        raise ExpectedToolError(msg)
    try:
        with target.open("ab"):
            pass
    except OSError as exc:
        msg = (
            f"cannot pipe to {output_path!r}: {exc.strerror or exc}. tmux "
            "would report success and capture nothing."
        )
        raise ExpectedToolError(msg) from exc


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
    """Log a pane's live output to a file (or stop an active log).

    Streams everything written to the pane (stdout plus terminal
    control sequences) into a file on disk — the common use is
    ``output_path="/tmp/pane.log"`` to capture scrollback continuously
    while the agent watches for errors. When ``output_path`` is given,
    starts logging; when ``output_path`` is None, stops any active pipe
    for the pane.

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
        pane.pipe()
        return f"Piping stopped for pane {pane.pane_id}"

    if not output_path.strip():
        msg = "output_path must be a non-empty path, or None to stop piping."
        raise ExpectedToolError(msg)

    redirect = ">>" if append else ">"
    # Two layers rewrite this string: tmux expands its own formats
    # first, then /bin/sh parses what is left. pipe-pane is the one
    # site on tmux's time-expanding path, so ``%`` needs escaping too.
    quoted = escape_format_time(shlex.quote(output_path))
    _raise_if_unwritable(output_path)
    pane.pipe(f"cat {redirect} {quoted}")
    return f"Piping pane {pane.pane_id} to {output_path}"
