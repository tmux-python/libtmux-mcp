"""Pipe-pane tool for streaming pane output to a file."""

from __future__ import annotations

import os
import pathlib
import re
import shlex

from libtmux_mcp._utils import (
    ExpectedToolError,
    _get_server,
    _resolve_pane,
    handle_tool_errors,
)

#: A maximal run of ``#``, plus the ``[`` that may follow it. tmux
#: treats a ``#``-run by what comes next, so the run is the unit that
#: has to be escaped -- not the individual ``#``.
_TMUX_HASH_RUN = re.compile(r"(#+)(\[?)")


def _escape_tmux_format(value: str) -> str:
    """Escape ``value`` so tmux's format expander reproduces it literally.

    ``pipe-pane`` runs its argument through the format expander before
    handing it to ``/bin/sh``, so :func:`shlex.quote` alone is not
    enough -- it guards the shell layer while tmux has already rewritten
    the string.

    There are TWO expansions to escape, not one. ``cmd-pipe-pane.c``
    calls ``format_expand_time()``, which runs the argument through
    ``strftime`` as well as the ``#``-format expander, so a ``%`` is as
    dangerous as a ``#``: ``100%done.log`` became ``10025one.log``
    (``%d`` -> day of month) and ``date-%Y.log`` became
    ``date-2026.log``. ``%%`` is strftime's literal escape and is safe
    to apply to every ``%``.

    Doubling every ``#`` is the obvious escape and it is wrong. A
    ``#``-run followed by ``[`` is a style sequence, reserved for
    ``format_draw``, and the expander copies the whole run through
    verbatim without ever collapsing it. Doubling there corrupts the
    path in exactly the way this function exists to prevent. Measured
    against tmux 3.7b:

    ==================  ==================  ==========================
    input               expands to          note
    ==================  ==================  ==========================
    ``#{pane_id}``      ``%0``              substituted
    ``##{pane_id}``     ``#{pane_id}``      run doubling escapes it
    ``####{a}``         ``##{a}``           composes for longer runs
    ``#S``              *(session name)*    legacy single-char alias
    ``#(echo hi)``      *(command job)*     substituted away
    ``##(echo hi)``     ``#(echo hi)``      run doubling escapes it
    ``#[fg=red]``       ``#[fg=red]``       verbatim
    ``##[fg=red]``      ``##[fg=red]``      verbatim -- never collapses
    ``issue ##42``      ``issue #42``       ordinary run collapses
    ==================  ==================  ==========================

    The legacy aliases are the easiest to trip over by accident: a log
    named ``#Session.log`` loses its ``#S`` to the session name and
    lands on ``<session>ession.log``.

    So: leave a run alone when ``[`` follows it, double it otherwise,
    and double every ``%`` for strftime.
    """

    def _escape_run(match: re.Match[str]) -> str:
        run, bracket = match.group(1), match.group(2)
        if bracket:
            return f"{run}{bracket}"
        return run * 2

    return _TMUX_HASH_RUN.sub(_escape_run, value).replace("%", "%%")


def _raise_if_unwritable(output_path: str) -> None:
    """Refuse a destination the redirect cannot possibly write.

    tmux hands the pipe command to a shell and reports success whatever
    that shell then does, so a redirect into a missing directory or an
    unwritable path fails silently and the caller is told its pane is
    being captured to a file that will never appear. Worse, a stale file
    already at that path then reads back as if it were live capture.

    Checked BEFORE piping rather than after. ``#{pane_pipe}`` looks like
    the obvious discriminator and is not: measured, it reads ``1``
    immediately after a doomed pipe, because the shell has been spawned
    and has not yet failed on the redirect. Only a later poll sees ``0``,
    so reading it here would be a check that never fires.
    """
    target = pathlib.Path(output_path)
    parent = target.parent
    if not parent.is_dir():
        msg = (
            f"cannot pipe to {output_path!r}: {str(parent)!r} is not an "
            "existing directory. tmux would report success and write "
            "nothing."
        )
        raise ExpectedToolError(msg)
    probe = target if target.exists() else parent
    if not os.access(probe, os.W_OK):
        msg = (
            f"cannot pipe to {output_path!r}: no write permission. tmux "
            "would report success and write nothing."
        )
        raise ExpectedToolError(msg)


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
    # Two layers rewrite this string, so it needs two escapes: tmux
    # expands its own formats first, then /bin/sh parses what is left.
    quoted = _escape_tmux_format(shlex.quote(output_path))
    _raise_if_unwritable(output_path)
    pane.pipe(f"cat {redirect} {quoted}")
    return f"Piping pane {pane.pane_id} to {output_path}"
