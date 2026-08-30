"""Tests for argument preconditions."""

from __future__ import annotations

from libtmux_mcp._guards import _unrunnable_spawn_program


def test_unrunnable_spawn_program_only_decides_what_it_can() -> None:
    """The pre-flight must refuse nothing sh would have run.

    tmux passes a one-argument command to ``$SHELL -c``, so shell
    syntax is beyond a pre-flight's reach. An earlier version checked
    ``shlex.split(shell)[0]`` against PATH and refused ``cd /tmp &&
    sleep 60``, ``VAR=1 sleep 60`` and ``exec sleep 60`` -- all three
    run -- while asserting the pane would die.
    """
    undecidable_or_fine = [
        "sleep 60",
        "/bin/sh",
        "cd /tmp && sleep 60",
        "VAR=1 sleep 60",
        "exec sleep 60",
        "echo hi; sleep 60",
        "",
    ]
    for shell in undecidable_or_fine:
        assert _unrunnable_spawn_program(shell) is None, shell

    for shell in ("/no/such/shell-xyz", "definitely-not-on-path-xyz", "-k"):
        assert _unrunnable_spawn_program(shell) == shell
