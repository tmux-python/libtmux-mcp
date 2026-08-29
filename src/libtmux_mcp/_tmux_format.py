"""Escaping for arguments tmux runs through its format expander.

Several tmux commands expand ``#{...}`` in an argument that the caller
meant literally, and they do it unconditionally -- there is no ``-F`` to
turn it off. A tool that promises "this is the name I will store" has to
escape, or it stores something else:

    rename_window(new_name="#{pane_current_path}")
      -> window named "/home/d/work/python/libtmux-mcp"

That is a disclosure vector, not just a wrong string: ``#{host}``,
``#{pane_pid}`` and friends all interpolate server state into a name
that shows up in the terminal and in ``list_panes``.

**Doubling every ``#`` is the obvious escape and it is wrong.** A
``#``-run followed by ``[`` is a style sequence reserved for
``format_draw``; the expander copies the whole run through verbatim and
never collapses it, so doubling corrupts the value it was meant to
protect. The unit that needs escaping is the *run*, not the ``#``.
Measured against tmux 3.7b / e802909d:

==================  ==================  ==========================
input               expands to          note
==================  ==================  ==========================
``#{pane_id}``      ``%0``              substituted
``##{pane_id}``     ``#{pane_id}``      run doubling escapes it
``####{a}``         ``##{a}``           composes for longer runs
``#S``              *(session name)*    legacy single-char alias
``#(echo hi)``      *(command job)*     substituted away
``#[fg=red]``       ``#[fg=red]``       verbatim
``##[fg=red]``      ``##[fg=red]``      verbatim -- never collapses
``issue ##42``      ``issue #42``       ordinary run collapses
==================  ==================  ==========================

**There are two expanders, and they disagree about ``%``.** Which one
runs is a property of the tmux command, so each call site has to say
which it is talking to:

===========================  ====================  ==============
tmux entry point             applies               ``%``
===========================  ====================  ==============
``format_expand()``          ``#``-formats         literal
``format_expand_time()``     ``#``-formats + also  strftime
                             ``strftime``
===========================  ====================  ==============

Only ``pipe-pane`` reaches the time-expanding one, via
``format_expand_time()`` in ``cmd-pipe-pane.c``. Everything else lands
on ``format_single`` -> ``format_expand``, where ``%`` is an ordinary
character -- ``select-pane -T '%Y-%m-%d'`` stores ``%Y-%m-%d``, so
doubling ``%`` there would corrupt it just as surely as doubling ``#``
before ``[``.
"""

from __future__ import annotations

import re

#: A maximal run of ``#`` immediately before ``(``. The run length
#: decides whether a job starts, so the run is what gets measured.
_TMUX_HASH_RUN_BEFORE_PAREN = re.compile(r"(#+)\(")

#: A maximal run of ``#``, plus the ``[`` that may follow it. tmux
#: treats a ``#``-run by what comes next, so the run is the unit that
#: has to be escaped -- not the individual ``#``.
_TMUX_HASH_RUN = re.compile(r"(#+)(\[?)")


def _escape_run(match: re.Match[str]) -> str:
    run, bracket = match.group(1), match.group(2)
    if bracket:
        return f"{run}{bracket}"
    return run * 2


def escape_format(value: str) -> str:
    """Escape ``value`` for a tmux argument expanded by ``format_expand``.

    Use for every command whose argument reaches ``format_single`` --
    ``rename-window``, ``rename-session``, ``select-pane -T``,
    ``new-window -n``, ``new-session -s/-n/-c``, ``set-option`` and
    ``show-options`` (the option *name*), and ``load-buffer`` (the
    *path*). Round-trips exactly: ``show_option(escape_format(x))``
    reads back ``x``.

    Parameters
    ----------
    value : str
        The literal the caller wants tmux to store or match.

    Returns
    -------
    str
        ``value`` with each ``#``-run doubled, except a run followed by
        ``[``. ``%`` is left alone -- see the module docstring.
    """
    return _TMUX_HASH_RUN.sub(_escape_run, value)


def escape_format_time(value: str) -> str:
    """Escape ``value`` for an argument expanded by ``format_expand_time``.

    Only ``pipe-pane`` needs this. Adds strftime escaping on top of
    :func:`escape_format`: an unescaped ``%`` is a strftime directive
    there, so ``100%done.log`` became ``10025one.log`` (``%d`` -> day of
    month) and ``date-%Y.log`` became ``date-2026.log``.

    Parameters
    ----------
    value : str
        The literal path the caller wants written.

    Returns
    -------
    str
        ``value`` with ``#``-runs escaped as in :func:`escape_format`,
        and every ``%`` doubled.
    """
    return escape_format(value).replace("%", "%%")


def contains_format_job(value: str) -> bool:
    """Whether ``value`` would start a ``#(command)`` job.

    The parity of the ``#``-run decides it, for the same reason the
    escaper doubles runs: ``format_expand1`` consumes ``#`` pairs into a
    literal ``#`` before it ever looks for ``(``, so only an ODD run
    leaves a bare ``#`` to open a job. Measured, and matching
    ``format.c``'s ``case '#'`` (emit a literal and continue) against
    its ``case '('`` (start a job):

    ==========  ==============  ==============
    input       renders as      runs a job
    ==========  ==============  ==============
    ``#(x)``    *(job output)*  yes
    ``##(x)``   ``#(x)``        no
    ``###(x)``  ``#`` + output  yes
    ``####(x)`` ``##(x)``       no
    ==========  ==============  ==============

    A bare ``"#(" in value`` test blocks all four, so a caller could not
    put a literal ``#(`` in a label or a code snippet even though tmux
    would render it harmlessly.

    Parameters
    ----------
    value : str
        A caller-supplied tmux format string.

    Returns
    -------
    bool
        True when any ``#``-run before ``(`` is odd-length.
    """
    return any(
        len(match.group(1)) % 2 for match in _TMUX_HASH_RUN_BEFORE_PAREN.finditer(value)
    )
