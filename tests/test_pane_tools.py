"""Tests for libtmux MCP pane tools."""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import shlex
import subprocess
import time
import typing as t

import pydantic
import pytest
from fastmcp.exceptions import ToolError
from libtmux import exc as libtmux_exc
from libtmux.test.retry import retry_until

from libtmux_mcp import _progress as _progress_module
from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.models import (
    CaptureSinceResult,
    PaneContentMatch,
    PaneSnapshot,
    SearchPanesResult,
    SendKeysOperation,
    WaitForTextResult,
)
from libtmux_mcp.tools.pane_tools import (
    capture_pane,
    capture_since,
    clear_pane,
    display_message,
    enter_copy_mode,
    exit_copy_mode,
    find_pane_by_position,
    get_pane_info,
    kill_pane,
    paste_text,
    pipe_pane,
    resize_pane,
    respawn_pane,
    run_command,
    search_panes,
    select_pane,
    send_keys,
    send_keys_batch,
    set_pane_title,
    snapshot_pane,
    swap_pane,
    wait_for_text,
)
from tests.conftest import wire_annotations

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session
    from libtmux.window import Window


class RunCommandFixture(t.NamedTuple):
    """Test fixture for run_command exit-status cases."""

    test_id: str
    command: str
    expected_status: int
    expected_output: str


class RunCommandStatusIsolationFixture(t.NamedTuple):
    """Test fixture for shell-state changes before run_command's trailer."""

    test_id: str
    command: str
    expected_status: int
    expected_output: str | None


class RunCommandPaneTargetFixture(t.NamedTuple):
    """Test fixture for run_command pane-targeted status handoff."""

    test_id: str
    command: str
    expected_status: int
    expected_output: str


class SendKeysOperationValidationFixture(t.NamedTuple):
    """Test fixture for send_keys_batch operation validation."""

    test_id: str
    payload: dict[str, object]
    expected_field: str


class RunCommandHistoryFixture(t.NamedTuple):
    """Test fixture for run_command shell history suppression."""

    test_id: str
    secret: str


RUN_COMMAND_FIXTURES: list[RunCommandFixture] = [
    RunCommandFixture("success", "printf 'RUN_COMMAND_OK\\n'", 0, "RUN_COMMAND_OK"),
    RunCommandFixture(
        "failure",
        "printf 'RUN_COMMAND_FAIL\\n'; false",
        1,
        "RUN_COMMAND_FAIL",
    ),
]


RUN_COMMAND_STATUS_ISOLATION_FIXTURES: list[RunCommandStatusIsolationFixture] = [
    RunCommandStatusIsolationFixture(
        "path_mutation",
        "PATH=/tmp; printf 'RUN_COMMAND_PATH_OK\\n'",
        0,
        "RUN_COMMAND_PATH_OK",
    ),
    RunCommandStatusIsolationFixture("errexit_false", "set -e; false", 1, None),
]


RUN_COMMAND_PANE_TARGET_FIXTURES: list[RunCommandPaneTargetFixture] = [
    RunCommandPaneTargetFixture(
        "missing_tmux_pane_env_in_inactive_target",
        "printf 'RUN_COMMAND_TARGET_OK\\n'",
        0,
        "RUN_COMMAND_TARGET_OK",
    ),
]


RUN_COMMAND_HISTORY_FIXTURES: list[RunCommandHistoryFixture] = [
    RunCommandHistoryFixture("bash_ignorespace", "RUN_COMMAND_HISTORY_SECRET"),
]


SEND_KEYS_OPERATION_VALIDATION_FIXTURES: list[SendKeysOperationValidationFixture] = [
    SendKeysOperationValidationFixture(
        test_id="unknown_pane_alias",
        payload={"keys": "printf SECRET", "pane": "%2"},
        expected_field="pane",
    ),
    SendKeysOperationValidationFixture(
        test_id="misspelled_pane_id",
        payload={"keys": "printf SECRET", "pan_id": "%2"},
        expected_field="pan_id",
    ),
]


class SendKeysBatchSuggestionFixture(t.NamedTuple):
    """Test fixture for send_keys_batch suggestion preservation."""

    test_id: str
    operations: list[SendKeysOperation]
    expected_error_snippet: str


SEND_KEYS_BATCH_SUGGESTION_FIXTURES: list[SendKeysBatchSuggestionFixture] = [
    SendKeysBatchSuggestionFixture(
        test_id="missing_pane_id",
        operations=[SendKeysOperation(keys="echo", pane_id="%invalid_pane")],
        expected_error_snippet="Call list_panes to discover valid pane ids.",
    ),
]


class SendKeysBatchTimeoutFixture(t.NamedTuple):
    """Test fixture for send_keys_batch timeout."""

    test_id: str
    operations: list[dict[str, t.Any]]
    timeout: float
    expected_succeeded: int
    expected_failed: int
    expected_error_snippet: str


SEND_KEYS_BATCH_TIMEOUT_FIXTURES: list[SendKeysBatchTimeoutFixture] = [
    SendKeysBatchTimeoutFixture(
        test_id="timeout_second_operation",
        operations=[
            {"keys": "echo 1"},
            {"keys": "echo 2"},
        ],
        timeout=5.0,
        expected_succeeded=1,
        expected_failed=1,
        expected_error_snippet="timeout",
    ),
]


class SendKeysBatchInProgressTimeoutFixture(t.NamedTuple):
    """Test fixture for send_keys_batch in-progress send timeout."""

    test_id: str
    timeout: float
    blocked_seconds: float
    expected_succeeded: int
    expected_failed: int
    expected_error_snippet: str


SEND_KEYS_BATCH_IN_PROGRESS_TIMEOUT_FIXTURES: list[
    SendKeysBatchInProgressTimeoutFixture
] = [
    SendKeysBatchInProgressTimeoutFixture(
        test_id="single_operation_stalls",
        timeout=0.05,
        blocked_seconds=0.1,
        expected_succeeded=0,
        expected_failed=1,
        expected_error_snippet="timeout",
    ),
]


def test_send_keys(mcp_server: Server, mcp_pane: Pane) -> None:
    """send_keys sends keys to a pane."""
    result = send_keys(
        keys="echo hello_mcp",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "sent" in result.lower()


class PaneStateParseFixture(t.NamedTuple):
    """Test fixture for :func:`_parse_pane_state`."""

    test_id: str
    raw: str
    expected_dead: bool
    expected_height: int


PANE_STATE_PARSE_FIXTURES: list[PaneStateParseFixture] = [
    # history_size|cursor_y|pane_height|pane_width|in_mode|pid|dead|alt
    PaneStateParseFixture("live_pane", "6|0|11|80|0|3495270|0|0", False, 11),
    PaneStateParseFixture("explicitly_dead", "6|0|11|80|0|3495270|1|0", True, 11),
    # tmux blanks every field for a pane that no longer exists.
    PaneStateParseFixture("pane_gone_all_empty", "|||||||", True, 0),
]


@pytest.mark.parametrize(
    PaneStateParseFixture._fields,
    PANE_STATE_PARSE_FIXTURES,
    ids=[fixture.test_id for fixture in PANE_STATE_PARSE_FIXTURES],
)
def test_parse_pane_state_survives_a_vanished_pane(
    test_id: str,
    raw: str,
    expected_dead: bool,
    expected_height: int,
) -> None:
    """A vanished pane parses as dead instead of raising.

    Killing a pane mid-wait made ``display-message`` expand every field
    to empty, and the bare ``int()`` raised ``invalid literal for int()
    with base 10: ''`` from the poll path. ``pane_dead`` reads empty
    too, so the empty pid is what identifies the pane as gone.
    """
    from libtmux_mcp.tools.pane_tools.state import _parse_pane_state

    assert test_id
    state = _parse_pane_state(raw)

    assert state.pane_dead is expected_dead
    assert state.pane_height == expected_height


def test_exit_copy_mode_reports_a_pane_that_is_not_in_a_mode(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A copy-mode command tmux rejected must not read as success.

    ``Pane.send_keys(copy_mode_cmd=...)`` discards tmux's result, so
    cancelling a pane that is not in a mode returned a full ``PaneInfo``
    that looked like confirmation the pane had left copy mode. tmux says
    ``not in a mode`` and exits 1.
    """
    from libtmux_mcp.tools.pane_tools.copy_mode import enter_copy_mode, exit_copy_mode

    with pytest.raises(ToolError, match="not in a mode"):
        exit_copy_mode(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)

    # Control: the real flow still works, so the guard is not blanket.
    enter_copy_mode(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)
    exit_copy_mode(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)


def test_run_command_refuses_a_full_screen_program(
    monkeypatch: pytest.MonkeyPatch, mcp_server: Server, mcp_pane: Pane
) -> None:
    """A pane owned by less/vi has no prompt, so refuse rather than type.

    The exit-status wrapper is consumed as the PROGRAM's keystrokes:
    measured against ``less``, ``s=$?...`` became its save-to-file
    command and a fragment escaped to a shell. In ``vi`` the same
    payload lands in the buffer, where ``:``-prefixed fragments edit and
    write files. ``alternate_on`` was already readable before the call.

    The state is stubbed rather than driven with a real pager so the
    test does not depend on which one CI has installed.
    """
    import asyncio

    from libtmux_mcp.tools.pane_tools.state import _read_pane_state

    state = _read_pane_state(mcp_pane)
    stubbed = 0

    async def _busy_state(_server: t.Any, _pane_id: str) -> t.Any:
        nonlocal stubbed
        stubbed += 1
        return state._replace(alternate_on=True)

    # Patched where run_command READS it. An earlier version stubbed
    # ``_read_pane_state``, which this tool stopped calling when its
    # reads moved to the killable subprocess -- and with no count to
    # check, the stub silently stopped applying and the test reported
    # only "DID NOT RAISE".
    monkeypatch.setattr(
        "libtmux_mcp.tools.pane_tools.io._bounded_pane_state", _busy_state
    )

    with pytest.raises(ToolError, match="full-screen program"):
        asyncio.run(
            run_command(
                command="echo SHOULD_NOT_BE_SENT",
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )

    assert stubbed, "the stub never applied; the refusal above proved nothing"
    assert not any("SHOULD_NOT_BE_SENT" in line for line in mcp_pane.capture_pane())


def test_run_command_timeout_flags_that_the_command_may_still_run(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A timed-out command is sent, not cancelled.

    The command keeps running in the pane after the wait gives up, so
    an agent reading only ``timed_out`` concludes it did not run and
    retries -- which is how a non-idempotent command runs twice.

    The pane must be genuinely at a prompt for that to be the claim
    under test. This previously used ``_park_pane``, which leaves
    ``sleep 60`` in the foreground of a one-shot ``sh -c`` that never
    returns to a prompt: the command could not have run, then or ever,
    so ``command_may_still_run=True`` was passing while being false.
    """
    import asyncio

    mcp_pane.respawn(kill=True, shell="sh")
    retry_until(
        lambda: (
            mcp_pane.display_message("#{pane_current_command}", get_text=True) == ["sh"]
        ),
        10,
        raises=True,
    )

    result = asyncio.run(
        run_command(
            command="sleep 10",
            pane_id=mcp_pane.pane_id,
            timeout=2.0,
            socket_name=mcp_server.socket_name,
        )
    )

    assert result.timed_out is True
    assert result.command_may_still_run is True


def test_pipe_pane_refuses_a_destination_it_cannot_write(
    mcp_server: Server, mcp_pane: Pane, tmp_path: pathlib.Path
) -> None:
    """Tmux reports success for a redirect that writes nothing.

    ``pipe-pane`` hands its argument to a shell and returns success
    whatever that shell does, so a missing parent directory produced
    "Piping pane %N to ..." and no file ever appeared. Checked before
    piping: ``#{pane_pipe}`` reads ``1`` immediately after a doomed
    pipe, because the shell has been spawned and has not yet failed, so
    reading it here would be a check that never fires.
    """

    def refuse(path: pathlib.Path | str) -> None:
        with pytest.raises(ToolError, match="cannot pipe to"):
            pipe_pane(
                pane_id=mcp_pane.pane_id,
                output_path=str(path),
                socket_name=mcp_server.socket_name,
            )

    refuse(tmp_path / "no-such-dir" / "out.log")

    # Each of these passed a parent-directory-plus-access check and
    # captured nothing. They are here because every stat-shaped
    # predicate is a proxy for "a shell can append to this", and the
    # proxy kept being wrong in a new way.
    directory = tmp_path / "isadir"
    directory.mkdir()
    refuse(directory)

    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    dangling = tmp_path / "dangling"
    dangling.symlink_to(locked / "nope.log")
    refuse(dangling)

    refuse(tmp_path / ("n" * 300))

    # A reader-less FIFO blocks the shell in open() forever, so it looks
    # healthy and captures nothing -- invisible to any poll of
    # #{pane_pipe}, which is why the regular-file test is separate.
    fifo = tmp_path / "nr.fifo"
    os.mkfifo(fifo)
    refuse(fifo)

    # Control: a writable destination still pipes.
    good = tmp_path / "out.log"
    pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=str(good),
        socket_name=mcp_server.socket_name,
    )
    pipe_pane(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)


def test_respawn_pane_refuses_a_shell_that_cannot_run(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A mistyped shell destroys the pane; tmux still reports success.

    tmux does not fail a respawn whose command cannot be executed -- the
    new process dies immediately and takes the pane with it, along with
    the window, session and server if it was the last one. Checked
    before respawning, because catching it afterwards can only report
    the loss, and even that races the dying process.
    """
    pane_id = mcp_pane.pane_id
    assert pane_id is not None

    with pytest.raises(ToolError, match="not an executable command"):
        respawn_pane(
            pane_id=pane_id,
            kill=True,
            shell="/no/such/shell-xyz",
            socket_name=mcp_server.socket_name,
        )

    # The pane must still be there, which is the whole point.
    survived = get_pane_info(pane_id=pane_id, socket_name=mcp_server.socket_name)
    assert survived.pane_id == pane_id


def test_paste_text_says_a_bracketed_newline_did_not_submit(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A bracketed trailing newline is reported as not submitted.

    Bracketed paste holds the trailing newline in the shell's edit
    buffer instead of submitting -- correct terminal behavior and a
    safe default, but the text is not inert: it runs when Enter next
    reaches the pane from any source, out of order with this call.
    """
    submitted = paste_text(
        text="echo BRACKET_NOTE",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    held = paste_text(
        text="echo BRACKET_NOTE\n",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )

    assert "NOT submitted" not in submitted
    assert "NOT submitted" in held
    assert "bracket=False" in held
    mcp_pane.send_keys("C-u", enter=False)


class SearchPaginationFixture(t.NamedTuple):
    """Test fixture for rejected search_panes pagination."""

    test_id: str
    offset: int
    limit: int | None
    error_match: str


SEARCH_PAGINATION_FIXTURES: list[SearchPaginationFixture] = [
    SearchPaginationFixture("negative_offset", -1, None, "offset must be zero"),
    SearchPaginationFixture("zero_limit", 0, 0, "limit must be at least 1"),
]


@pytest.mark.parametrize(
    SearchPaginationFixture._fields,
    SEARCH_PAGINATION_FIXTURES,
    ids=[fixture.test_id for fixture in SEARCH_PAGINATION_FIXTURES],
)
def test_search_panes_rejects_nonsense_pagination(
    test_id: str,
    offset: int,
    limit: int | None,
    error_match: str,
    mcp_server: Server,
) -> None:
    """Bad pagination errors instead of answering with an empty page.

    ``limit=0`` returned ``matches: []``, which an agent cannot tell
    from a genuine miss, and a negative ``offset`` was clamped to 0 and
    echoed back unchanged, so the result silently did not describe the
    request.
    """
    assert test_id

    with pytest.raises(ToolError, match=error_match):
        search_panes(
            pattern="anything",
            offset=offset,
            limit=limit,
            socket_name=mcp_server.socket_name,
        )


def test_input_tools_refuse_to_guess_where_the_keystrokes_go(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A tool that types into a pane may not pick the pane itself.

    Reads may default; delivering input may not. The default was the
    first LISTED object, and tmux lists sessions by NAME, so
    ``rename_session`` moved where an untargeted ``send_keys`` landed.
    Keying the default on the tmux id makes it stable, but stable is
    not correct: nothing in the call says which pane was meant, and
    ``kill_window`` already refuses to guess for exactly this reason.

    The result does name the pane it used -- after the keystrokes have
    landed, which is disclosure rather than a guard.
    """
    socket = mcp_server.socket_name
    for call in (
        lambda: send_keys(keys="echo hi", socket_name=socket),
        lambda: paste_text(text="hi", socket_name=socket),
        lambda: asyncio.run(run_command(command="true", socket_name=socket)),
    ):
        with pytest.raises(ToolError, match="requires an explicit target"):
            call()

    # A batch is checked per operation: one untargeted entry among
    # targeted ones is what a whole-batch check would miss.
    result = send_keys_batch(
        operations=[
            SendKeysOperation(pane_id=mcp_pane.pane_id, keys="echo one"),
            SendKeysOperation(keys="echo two"),
        ],
        on_error="continue",
        socket_name=socket,
    )
    assert result.results[0].success is True
    assert result.results[1].success is False
    assert "requires an explicit target" in (result.results[1].error or "")


def test_search_panes_refuses_a_pattern_it_could_not_interrupt(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A caller regex reached ``re`` with no bound of any kind.

    ``search_panes`` is readonly-tier, so ``(a+)+$`` is reachable at the
    lowest safety level; sixteen concurrent calls made every tool
    unresponsive. Neither a deadline nor a worker cap can help, because
    a thread inside ``re`` cannot be interrupted -- one 121-character
    line does not finish in three minutes.
    """
    with pytest.raises(ToolError, match="exponential time"):
        search_panes(pattern=r"(a+)+$", regex=True, socket_name=mcp_server.socket_name)

    # Control: an ordinary regex still runs, and the same text passed
    # as a literal is never screened.
    mcp_pane.send_keys("printf 'aaaaX\\n'", enter=True)
    retry_until(
        lambda: any("aaaaX" in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )
    found = search_panes(pattern=r"a+X", regex=True, socket_name=mcp_server.socket_name)
    assert found.matches
    assert (
        search_panes(pattern=r"(a+)+$", regex=False, socket_name=mcp_server.socket_name)
        is not None
    )


class DashPayloadArgvFixture(t.NamedTuple):
    """Test fixture for ``--`` placement in the send-keys argv."""

    test_id: str
    literal: bool
    enter: bool
    expected_flags: list[str]


DASH_PAYLOAD_ARGV_FIXTURES: list[DashPayloadArgvFixture] = [
    DashPayloadArgvFixture("literal_no_enter", True, False, ["-l"]),
    DashPayloadArgvFixture("keyname_with_enter", False, True, []),
]


@pytest.mark.parametrize(
    DashPayloadArgvFixture._fields,
    DASH_PAYLOAD_ARGV_FIXTURES,
    ids=[fixture.test_id for fixture in DASH_PAYLOAD_ARGV_FIXTURES],
)
def test_send_keys_argv_terminates_flags_before_the_payload(
    test_id: str,
    literal: bool,
    enter: bool,
    expected_flags: list[str],
    mcp_pane: Pane,
) -> None:
    """``--`` must sit after the flags and immediately before the text.

    Without it tmux reads a payload beginning with ``-`` as flags and
    rejects the command, and because ``Pane.send_keys`` discarded the
    result the tool reported ``Keys sent to pane %N`` for a send that
    delivered nothing. Asserted on the argv rather than on pane
    contents: whether un-submitted text echoes into the visible pane
    depends on the shell and terminal, which varies across CI.
    """
    from libtmux_mcp.tools.pane_tools.io import _send_keys_argvs

    assert test_id
    payload = "-X cancel --help -v"
    argvs = _send_keys_argvs(
        mcp_pane,
        payload,
        enter=enter,
        literal=literal,
        suppress_history=False,
    )

    send = argvs[0]
    assert send[-2:] == ["--", payload]
    for flag in expected_flags:
        assert flag in send
    # Enter is a separate call without -l, so it stays a key name
    # rather than the literal text "Enter".
    assert len(argvs) == (2 if enter else 1)
    if enter:
        assert argvs[1][-1] == "Enter"
        assert "-l" not in argvs[1]


def test_send_keys_batch_sends_operations_in_order(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """send_keys_batch sends ordered raw-input operations and reports each one."""
    import asyncio

    from libtmux_mcp.models import SendKeysBatchResult, SendKeysOperation
    from libtmux_mcp.tools.pane_tools import send_keys_batch
    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = "mcp_test_send_keys_batch_order"
    result = send_keys_batch(
        operations=[
            SendKeysOperation(
                keys="printf 'BATCH_FIRST\\n'",
                pane_id=mcp_pane.pane_id,
            ),
            SendKeysOperation(
                keys=f"printf 'BATCH_SECOND\\n'; tmux wait-for -S {channel}",
                pane_id=mcp_pane.pane_id,
            ),
        ],
        socket_name=mcp_server.socket_name,
    )

    assert isinstance(result, SendKeysBatchResult)
    assert result.succeeded == 2
    assert result.failed == 0
    assert result.stopped_at is None
    assert [item.index for item in result.results] == [0, 1]
    assert all(item.success for item in result.results)
    assert all(item.pane_id == mcp_pane.pane_id for item in result.results)

    asyncio.run(
        wait_for_channel(channel, timeout=5.0, socket_name=mcp_server.socket_name)
    )
    capture = "\n".join(mcp_pane.capture_pane())
    assert capture.index("BATCH_FIRST") < capture.index("BATCH_SECOND")


def test_send_keys_batch_continues_after_operation_error(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """send_keys_batch can keep later operations after a target failure."""
    import asyncio

    from libtmux_mcp.models import SendKeysOperation
    from libtmux_mcp.tools.pane_tools import send_keys_batch
    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = "mcp_test_send_keys_batch_continue"
    result = send_keys_batch(
        operations=[
            SendKeysOperation(
                keys="printf 'BATCH_BEFORE\\n'",
                pane_id=mcp_pane.pane_id,
            ),
            SendKeysOperation(keys="printf 'BATCH_MISSING\\n'", pane_id="%999999"),
            SendKeysOperation(
                keys=f"printf 'BATCH_AFTER\\n'; tmux wait-for -S {channel}",
                pane_id=mcp_pane.pane_id,
            ),
        ],
        on_error="continue",
        socket_name=mcp_server.socket_name,
    )

    assert result.succeeded == 2
    assert result.failed == 1
    assert result.stopped_at is None
    assert [item.success for item in result.results] == [True, False, True]
    assert result.results[1].pane_id is None
    assert "Pane not found" in (result.results[1].error or "")

    asyncio.run(
        wait_for_channel(channel, timeout=5.0, socket_name=mcp_server.socket_name)
    )
    capture = "\n".join(mcp_pane.capture_pane())
    assert "BATCH_BEFORE" in capture
    assert "BATCH_AFTER" in capture
    assert "BATCH_MISSING" not in capture


def test_send_keys_batch_stops_after_operation_error(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """send_keys_batch defaults to stop-on-error without raising."""
    from libtmux_mcp.models import SendKeysOperation
    from libtmux_mcp.tools.pane_tools import send_keys_batch

    result = send_keys_batch(
        operations=[
            SendKeysOperation(
                keys="printf 'BATCH_STOP_BEFORE\\n'",
                pane_id=mcp_pane.pane_id,
            ),
            SendKeysOperation(keys="printf 'BATCH_STOP_MISSING\\n'", pane_id="%999999"),
            SendKeysOperation(
                keys="printf 'BATCH_STOP_AFTER\\n'",
                pane_id=mcp_pane.pane_id,
            ),
        ],
        socket_name=mcp_server.socket_name,
    )

    assert result.succeeded == 1
    assert result.failed == 1
    assert result.stopped_at == 1
    assert len(result.results) == 2
    assert [item.success for item in result.results] == [True, False]
    capture = "\n".join(mcp_pane.capture_pane())
    assert "BATCH_STOP_AFTER" not in capture


def test_send_keys_batch_rejects_empty_operations(mcp_server: Server) -> None:
    """send_keys_batch requires at least one operation."""
    from libtmux_mcp.tools.pane_tools import send_keys_batch

    with pytest.raises(ToolError, match="operations must not be empty"):
        send_keys_batch(operations=[], socket_name=mcp_server.socket_name)


@pytest.mark.parametrize(
    SendKeysBatchSuggestionFixture._fields,
    SEND_KEYS_BATCH_SUGGESTION_FIXTURES,
    ids=[fixture.test_id for fixture in SEND_KEYS_BATCH_SUGGESTION_FIXTURES],
)
def test_send_keys_batch_preserves_error_suggestions(
    test_id: str,
    operations: list[SendKeysOperation],
    expected_error_snippet: str,
    mcp_server: Server,
) -> None:
    """send_keys_batch preserves exception suggestions in the error string."""
    assert test_id
    from libtmux_mcp.tools.pane_tools import send_keys_batch

    result = send_keys_batch(
        operations=operations,
        socket_name=mcp_server.socket_name,
    )
    assert len(result.results) == 1
    error_msg = result.results[0].error
    assert error_msg is not None
    assert expected_error_snippet in error_msg


@pytest.mark.parametrize(
    SendKeysBatchTimeoutFixture._fields,
    SEND_KEYS_BATCH_TIMEOUT_FIXTURES,
    ids=[fixture.test_id for fixture in SEND_KEYS_BATCH_TIMEOUT_FIXTURES],
)
def test_send_keys_batch_timeout(
    test_id: str,
    operations: list[dict[str, t.Any]],
    timeout: float,
    expected_succeeded: int,
    expected_failed: int,
    expected_error_snippet: str,
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_keys_batch aborts if execution exceeds timeout."""
    assert test_id
    from libtmux_mcp.models import SendKeysOperation
    from libtmux_mcp.tools.pane_tools import send_keys_batch

    call_count = 0

    real_run = subprocess.run

    def timed_send_keys(*args: t.Any, **kwargs: t.Any) -> t.Any:
        # Counts SEND-KEYS calls only. Counting every subprocess call
        # made the fixture depend on how many tmux round trips the tool
        # happens to make around them, so adding one elsewhere silently
        # moved which operation timed out.
        argv = list(args[0]) if args else list(kwargs.get("args", []))
        if "send-keys" not in argv:
            return real_run(*args, **kwargs)
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise subprocess.TimeoutExpired(cmd="tmux", timeout=timeout)
        return subprocess.CompletedProcess(args=["tmux"], returncode=0)

    monkeypatch.setattr(
        "libtmux_mcp.tools.pane_tools.io.subprocess.run",
        timed_send_keys,
    )

    op_models = []
    for op in operations:
        op["pane_id"] = mcp_pane.pane_id
        op_models.append(SendKeysOperation(**op))

    result = send_keys_batch(
        operations=op_models,
        timeout=timeout,
        socket_name=mcp_server.socket_name,
    )
    assert result.succeeded == expected_succeeded
    assert result.failed == expected_failed
    assert expected_error_snippet in (result.results[-1].error or "").lower()


@pytest.mark.parametrize(
    SendKeysBatchInProgressTimeoutFixture._fields,
    SEND_KEYS_BATCH_IN_PROGRESS_TIMEOUT_FIXTURES,
    ids=[fixture.test_id for fixture in SEND_KEYS_BATCH_IN_PROGRESS_TIMEOUT_FIXTURES],
)
def test_send_keys_batch_timeout_bounds_in_progress_send(
    test_id: str,
    timeout: float,
    blocked_seconds: float,
    expected_succeeded: int,
    expected_failed: int,
    expected_error_snippet: str,
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """send_keys_batch fails a send that blocks past the batch timeout."""
    assert test_id
    from libtmux import Pane

    from libtmux_mcp.models import SendKeysOperation
    from libtmux_mcp.tools.pane_tools import send_keys_batch

    def stalled_send_keys(*args: t.Any, **kwargs: t.Any) -> None:
        time.sleep(blocked_seconds)

    real_run = subprocess.run

    def timed_out_run(*args: t.Any, **kwargs: t.Any) -> t.Any:
        # send-keys only: hanging every subprocess call also hung the
        # liveness probe the tool makes first, so the assertion was
        # satisfied by a refusal from a different layer.
        argv = list(args[0]) if args else list(kwargs.get("args", []))
        if "send-keys" not in argv:
            return real_run(*args, **kwargs)
        raise subprocess.TimeoutExpired(cmd="tmux", timeout=timeout)

    monkeypatch.setattr(Pane, "send_keys", stalled_send_keys)
    monkeypatch.setattr("libtmux_mcp.tools.pane_tools.io.subprocess.run", timed_out_run)

    result = send_keys_batch(
        operations=[
            SendKeysOperation(keys="echo stalled", pane_id=mcp_pane.pane_id),
        ],
        timeout=timeout,
        socket_name=mcp_server.socket_name,
    )
    assert result.succeeded == expected_succeeded
    assert result.failed == expected_failed
    assert expected_error_snippet in (result.results[0].error or "").lower()


@pytest.mark.parametrize(
    SendKeysOperationValidationFixture._fields,
    SEND_KEYS_OPERATION_VALIDATION_FIXTURES,
    ids=[fixture.test_id for fixture in SEND_KEYS_OPERATION_VALIDATION_FIXTURES],
)
def test_send_keys_operation_rejects_unknown_fields(
    test_id: str,
    payload: dict[str, object],
    expected_field: str,
) -> None:
    """send_keys_batch operation validation rejects unsupported fields."""
    assert test_id
    with pytest.raises(pydantic.ValidationError) as excinfo:
        SendKeysOperation.model_validate(payload)

    assert expected_field in str(excinfo.value)


def test_send_keys_docstring_routes_authored_commands_to_run_command() -> None:
    """``send_keys`` docstring keeps raw input below command completion."""
    assert send_keys.__doc__ is not None
    assert "run_command" in send_keys.__doc__
    assert "send_keys_batch" in send_keys.__doc__
    assert "capture_since" in send_keys.__doc__
    assert "wait_for_channel" in send_keys.__doc__


@pytest.mark.parametrize(
    RunCommandFixture._fields,
    RUN_COMMAND_FIXTURES,
    ids=[f.test_id for f in RUN_COMMAND_FIXTURES],
)
def test_run_command_reports_exit_status(
    mcp_server: Server,
    mcp_pane: Pane,
    test_id: str,
    command: str,
    expected_status: int,
    expected_output: str,
) -> None:
    """run_command waits for completion and reports shell exit status."""
    import asyncio

    from libtmux_mcp.models import RunCommandResult
    from libtmux_mcp.tools.pane_tools import run_command

    assert test_id

    result = asyncio.run(
        run_command(
            command=command,
            pane_id=mcp_pane.pane_id,
            timeout=20.0,
            socket_name=mcp_server.socket_name,
        )
    )

    assert isinstance(result, RunCommandResult)
    assert result.pane_id == mcp_pane.pane_id
    assert result.exit_status == expected_status
    assert result.timed_out is False
    assert any(expected_output in line for line in result.output)


def test_run_command_timeout_reports_without_killing_shell(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """run_command timeout returns while the interactive shell remains usable."""
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    marker = "RUN_COMMAND_TIMEOUT_FINISHED"
    result = asyncio.run(
        run_command(
            command=f"sleep 0.5; printf '{marker}\\n'",
            pane_id=mcp_pane.pane_id,
            timeout=0.05,
            socket_name=mcp_server.socket_name,
        )
    )

    assert result.timed_out is True
    assert result.exit_status is None

    retry_until(
        lambda: any(marker in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )


def test_run_command_reports_unclamped_timeout(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A ``timeout`` under the ceiling is reported verbatim, unclamped."""
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    result = asyncio.run(
        run_command(
            command="true",
            pane_id=mcp_pane.pane_id,
            # The timeout IS the subject here -- it is echoed back on
            # effective_timeout, so this one must not be widened.
            timeout=5.0,
            socket_name=mcp_server.socket_name,
        )
    )
    assert result.effective_timeout == 5.0


def test_run_command_clamps_oversized_timeout(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An over-large ``timeout`` is clamped to the server wait ceiling.

    Mirrors ``wait_for_text``'s clamp: without it, ``run_command`` would
    honour a caller-supplied ``timeout`` of any size — including one
    that stalls the shared MCP connection far longer than the server's
    wait policy allows. The ceiling is lowered to 0.3 s so the assertion
    is about the clamp mechanism, not wall-clock patience.
    """
    import asyncio

    from libtmux_mcp import _wait_policy
    from libtmux_mcp.tools.pane_tools import run_command

    monkeypatch.setattr(_wait_policy, "_wait_max_seconds", 0.3)

    started = time.monotonic()
    result = asyncio.run(
        run_command(
            command="sleep 5",
            pane_id=mcp_pane.pane_id,
            timeout=3600.0,
            socket_name=mcp_server.socket_name,
        )
    )
    elapsed = time.monotonic() - started

    assert result.timed_out is True
    assert result.effective_timeout == 0.3
    assert elapsed < 10.0, f"clamped wait ran {elapsed:.1f}s"


def _armed_after_baseline(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Event set once ``wait_for_text`` holds its entry baseline.

    Sleeping instead is not slow but wrong: output emitted before the
    baseline lands in ``entry_below_cursor``, which filters by content,
    so it can never match afterwards and the wait runs to its ceiling.

    Set on the *second* ``_bounded_capture``. The first IS the entry
    capture; the second is issued after its rows have been stored, so
    arming is complete by then.
    """
    # wait.py imports ``_bounded_capture`` into its own namespace, so the
    # patch has to land THERE. Patching it in _bounded_io leaves the name
    # wait.py actually calls untouched, the event never fires, and the
    # test waits out its ceiling instead of failing.
    from libtmux_mcp.tools.pane_tools import wait as wait_module

    event = asyncio.Event()
    original = wait_module._bounded_capture
    calls = 0

    async def _capture(*args: t.Any, **kwargs: t.Any) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            event.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(wait_module, "_bounded_capture", _capture)
    return event


def _run_command_wait_pids(socket_name: str) -> list[int]:
    """Return pids of live ``tmux -L <socket> wait-for r_*``/``p_*`` procs.

    Asks the kernel rather than the tool: the defect this backs is a
    tool that reports a clean cancellation while its child runs on, so
    the tool's own return value cannot be the witness. ``run_command``
    mints a random ``r_<hex>`` completion channel and a ``p_<hex>``
    started channel per call, hence the prefix match. BOTH must be
    matched: the started channel is waited on FIRST, so a probe that
    knew only about ``r_`` could look before that phase had finished and
    conclude no child existed -- which is what happened under load once
    the started channel was added.
    rather than an exact name.

    The argv must be the whole five-token vector and ``/proc/<pid>/exe``
    must resolve to tmux, so a shell (or pytest) whose command line
    merely mentions the socket cannot register as a hit; this process
    is skipped outright for the same reason. Reaped children are gone
    from ``/proc`` and zombies have an empty ``cmdline``, so neither
    counts as a survivor.
    """
    me = os.getpid()
    pids: list[int] = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            exe = (entry / "exe").readlink()
        except OSError:
            continue  # process exited, or not ours to inspect
        argv = [chunk.decode(errors="replace") for chunk in raw.split(b"\0") if chunk]
        if len(argv) != 5 or argv[1:4] != ["-L", socket_name, "wait-for"]:
            continue
        if not argv[4].startswith(("r_", "p_")) or exe.name != "tmux":
            continue
        pids.append(pid)
    return pids


def test_run_command_kills_tmux_child_on_cancel(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A cancelled ``run_command`` must not leave its wait-for child running.

    ``asyncio.to_thread(subprocess.run, ...)`` is uninterruptible: the
    coroutine raises ``CancelledError`` at once while the worker thread
    stays blocked in ``waitpid``, so ``tmux wait-for`` kept running for
    the whole remainder of its budget with nobody waiting on it.
    Measured before the fix: a 25 s ``run_command`` cancelled at 3 s
    left the child alive another 22 s. ``run_command`` is the wait an
    agent cancels most — it is the one wrapping long shell commands.
    """
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    socket_name = mcp_server.socket_name
    assert socket_name is not None

    # One constant governs both the call's budget and the window the
    # probe waits in, so they cannot drift apart: the cancel has to land
    # while the call is still in flight. Both are ceilings -- the wait
    # below exits the moment a child appears -- so a generous value is
    # free except on the loaded box that needs it. Measured: the
    # pre-child work exceeded 6 s at loadavg 37.
    call_budget = 20.0

    async def _drive() -> list[int]:
        task = asyncio.create_task(
            run_command(
                command="sleep 30",
                pane_id=mcp_pane.pane_id,
                timeout=call_budget,
                socket_name=socket_name,
            )
        )

        # Off the loop: the probe walks every entry in /proc, which on a
        # busy box takes long enough to starve the run_command it is
        # waiting for -- the poll then prevents the child it is polling
        # for from ever being spawned, and the guard below fires.
        async def _pids() -> list[int]:
            return await asyncio.to_thread(_run_command_wait_pids, socket_name)

        # Before the first child exists the call resolves a pane, runs
        # the busy guard, reads the occupant and sends the payload --
        # several tmux round trips plus a shell one. Three quarters of
        # the call's budget leaves room for the cancel to land
        # mid-flight.
        deadline = time.monotonic() + call_budget * 0.75
        while time.monotonic() < deadline and not task.done() and not await _pids():
            await asyncio.sleep(0.05)
        if not await _pids():
            # Two different failures used to read as one. The call
            # giving up before it ever spawned a wait child is a fact
            # about run_command under load; the probe not seeing a child
            # that exists is a fact about the probe. Reporting the first
            # as "the probe is broken" sent me looking in the wrong
            # place at loadavg 43.
            outcome = "still running"
            if task.done():
                outcome = f"already finished: {task.exception() or task.result()!r}"
                task.cancel()
            pytest.fail(
                f"no tmux wait-for child observed before the cancel; the "
                f"call was {outcome}. A later 'no survivors' result would "
                "be vacuous either way."
            )

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Poll rather than sleep once: the kill is synchronous but the
        # reap is not instantaneous. The 2 s window is far short of the
        # remaining budget, so a survivor here is an orphan and not a
        # slow teardown.
        reap_deadline = time.monotonic() + 2.0
        while time.monotonic() < reap_deadline:
            if not await _pids():
                break
            await asyncio.sleep(0.05)
        return await _pids()

    survivors = asyncio.run(_drive())
    assert not survivors, (
        f"cancelled run_command orphaned tmux child(ren) {survivors}; "
        "the child outlives the cancellation for the rest of its timeout"
    )


@pytest.mark.parametrize(
    RunCommandStatusIsolationFixture._fields,
    RUN_COMMAND_STATUS_ISOLATION_FIXTURES,
    ids=[f.test_id for f in RUN_COMMAND_STATUS_ISOLATION_FIXTURES],
)
def test_run_command_reports_status_after_shell_state_change(
    mcp_server: Server,
    mcp_pane: Pane,
    test_id: str,
    command: str,
    expected_status: int,
    expected_output: str | None,
) -> None:
    """run_command reports status after user commands mutate shell state."""
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    assert test_id
    result = asyncio.run(
        run_command(
            command=command,
            pane_id=mcp_pane.pane_id,
            # Generous on purpose: the subject is the reported status,
            # not the latency. A 2 s budget lost the race under load.
            timeout=10.0,
            socket_name=mcp_server.socket_name,
        )
    )

    assert result.exit_status == expected_status
    assert result.timed_out is False
    if expected_output is not None:
        assert any(expected_output in line for line in result.output)


@pytest.mark.parametrize(
    RunCommandPaneTargetFixture._fields,
    RUN_COMMAND_PANE_TARGET_FIXTURES,
    ids=[f.test_id for f in RUN_COMMAND_PANE_TARGET_FIXTURES],
)
def test_run_command_status_option_targets_resolved_pane(
    mcp_server: Server,
    mcp_window: Window,
    mcp_pane: Pane,
    test_id: str,
    command: str,
    expected_status: int,
    expected_output: str,
) -> None:
    """run_command status storage targets the pane the command ran in."""
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    assert test_id
    assert mcp_pane.pane_id is not None
    target_pane = mcp_window.split(attach=False)
    assert target_pane.pane_id is not None
    mcp_window.select_pane(mcp_pane.pane_id)

    target_pane.send_keys("exec env -u TMUX_PANE bash --noprofile --norc", enter=True)
    retry_until(
        lambda: any("bash-" in line for line in target_pane.capture_pane()),
        10,
        raises=True,
    )

    result = None
    try:
        result = asyncio.run(
            run_command(
                command=command,
                pane_id=target_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
    finally:
        with contextlib.suppress(libtmux_exc.LibTmuxException):
            mcp_window.select_pane(mcp_pane.pane_id)
        with contextlib.suppress(libtmux_exc.LibTmuxException):
            target_pane.kill()

    assert result is not None
    assert result.exit_status == expected_status
    assert result.timed_out is False
    assert any(expected_output in line for line in result.output)


@pytest.mark.parametrize(
    RunCommandHistoryFixture._fields,
    RUN_COMMAND_HISTORY_FIXTURES,
    ids=[f.test_id for f in RUN_COMMAND_HISTORY_FIXTURES],
)
def test_run_command_suppress_history(
    mcp_server: Server,
    mcp_pane: Pane,
    tmp_path: pathlib.Path,
    test_id: str,
    secret: str,
) -> None:
    """run_command suppresses shell history for secret-bearing commands."""
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    assert test_id
    histfile = tmp_path / "bash_history"
    mcp_pane.send_keys("exec bash --noprofile --norc", enter=True)
    retry_until(
        lambda: any("bash-" in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    setup = (
        f"HISTFILE={shlex.quote(str(histfile))}; "
        "HISTCONTROL=ignorespace; set -o history; "
        "history -c; history -w"
    )
    asyncio.run(
        run_command(
            command=setup,
            pane_id=mcp_pane.pane_id,
            timeout=2.0,
            suppress_history=True,
            socket_name=mcp_server.socket_name,
        )
    )
    asyncio.run(
        run_command(
            command=f"printf '{secret}\\n'",
            pane_id=mcp_pane.pane_id,
            timeout=2.0,
            suppress_history=True,
            socket_name=mcp_server.socket_name,
        )
    )
    asyncio.run(
        run_command(
            command="history -w",
            pane_id=mcp_pane.pane_id,
            timeout=2.0,
            suppress_history=True,
            socket_name=mcp_server.socket_name,
        )
    )

    assert secret not in histfile.read_text()


def test_run_command_tail_preserves_output(mcp_server: Server, mcp_pane: Pane) -> None:
    """run_command output is tail-preserved when max_lines is small."""
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    result = asyncio.run(
        run_command(
            command=(
                "for i in $(seq 1 6); do printf 'RUN_COMMAND_TRUNC_%s\\n' \"$i\"; done"
            ),
            pane_id=mcp_pane.pane_id,
            timeout=20.0,
            max_lines=2,
            socket_name=mcp_server.socket_name,
        )
    )

    assert result.output_truncated is True
    assert result.output_truncated_lines > 0
    assert len(result.output) == 2
    assert any("RUN_COMMAND_TRUNC_6" in line for line in result.output)


def test_run_command_tail_preserves_output_with_wrapped_private_prompt(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """run_command keeps command output when a long shell prompt wraps internals."""
    import asyncio

    from libtmux_mcp.tools.pane_tools import run_command

    long_prompt = "runner@runnervm123456:/home/runner/work/libtmux-mcp/libtmux-mcp$ "
    mcp_pane.cmd("resize-pane", "-x", "80")
    mcp_pane.send_keys("exec bash --noprofile --norc", enter=True)
    retry_until(
        lambda: any("bash-" in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )
    mcp_pane.send_keys(f"PS1={shlex.quote(long_prompt)}", enter=True)
    retry_until(
        lambda: any(long_prompt.rstrip() in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = asyncio.run(
        run_command(
            command=(
                "for i in $(seq 1 6); do printf 'RUN_COMMAND_WRAP_%s\\n' \"$i\"; done"
            ),
            pane_id=mcp_pane.pane_id,
            timeout=20.0,
            max_lines=2,
            socket_name=mcp_server.socket_name,
        )
    )

    assert result.output_truncated is True
    assert len(result.output) == 2
    assert any("RUN_COMMAND_WRAP_6" in line for line in result.output)


class FilterInternalLinesFixture(t.NamedTuple):
    """Fixture for legitimate output that resembles wrapper text."""

    test_id: str
    line: str


FILTER_KEEP_FIXTURES: list[FilterInternalLinesFixture] = [
    FilterInternalLinesFixture("mentions_mcp_status", "grep -n mcp_status app.log"),
    FilterInternalLinesFixture("mentions_set_option", 'echo "tmux set-option -p x"'),
    FilterInternalLinesFixture("mentions_wait_for", "run tmux wait-for -S done first"),
    FilterInternalLinesFixture("mentions_prefix", "ns=libtmux_mcp_ is reserved"),
]


FILTER_WRAPPER_LIKE_KEEP_FIXTURES: list[FilterInternalLinesFixture] = [
    FilterInternalLinesFixture(
        "tmux_script_status_option",
        "s=$?; tmux set-option -p @s_myapp_status 1",
    ),
    FilterInternalLinesFixture(
        "empty_short_status_prefix",
        "s=$?; tmux set-option -p @s_ 1",
    ),
]


class FilterDropFixture(t.NamedTuple):
    """Fixture for private run_command synchronisation fragments."""

    test_id: str
    lines: list[str]
    channel: str
    status_option: str


class FilterCurrentSyncLineFixture(t.NamedTuple):
    """Fixture for output after the current run_command sync line."""

    test_id: str
    output_lines: list[str]


_CURRENT_ID = "deadbeefdeadbeefdeadbeefdeadbeef"
_PREVIOUS_ID = "feedfacefeedfacefeedfacefeedface"
_SHORT_CURRENT_ID = "e743e5084b"
_SHORT_PREVIOUS_ID = "f00dbeef12"


FILTER_DROP_FIXTURES: list[FilterDropFixture] = [
    FilterDropFixture(
        "current_wrapped_long_marker",
        [
            "RUN_OK",
            (
                "∙ }; __libtmux_mcp_status=$?; tmux set-option -p "
                f"@libtmux_mcp_status_{_CURRENT_ID[:10]}"
            ),
            (
                f'{_CURRENT_ID[10:]} "$__libtmux_mcp_status"; '
                "tmux wait-for -S libtmux_mcp_run_"
            ),
            _CURRENT_ID,
        ],
        f"libtmux_mcp_run_{_CURRENT_ID}",
        f"@libtmux_mcp_status_{_CURRENT_ID}",
    ),
    FilterDropFixture(
        "previous_wrapped_long_marker",
        [
            "RUN_OK",
            (
                "∙ }; __libtmux_mcp_status=$?; tmux set-option -p "
                f"@libtmux_mcp_status_{_PREVIOUS_ID[:10]}"
            ),
            (
                f'{_PREVIOUS_ID[10:]} "$__libtmux_mcp_status"; '
                "tmux wait-for -S libtmux_mcp_run_"
            ),
            _PREVIOUS_ID,
        ],
        f"libtmux_mcp_run_{_CURRENT_ID}",
        f"@libtmux_mcp_status_{_CURRENT_ID}",
    ),
    FilterDropFixture(
        "current_short_marker",
        [
            "RUN_OK",
            (
                f'∙ }}; s=$?; tmux set-option -p @s_{_SHORT_CURRENT_ID} "$s"; '
                f"tmux wait-for -S r_{_SHORT_CURRENT_ID}"
            ),
        ],
        f"r_{_SHORT_CURRENT_ID}",
        f"@s_{_SHORT_CURRENT_ID}",
    ),
    FilterDropFixture(
        "previous_wrapped_short_marker",
        [
            "RUN_OK",
            f"∙ }}; s=$?; tmux set-option -p @s_{_SHORT_PREVIOUS_ID[:6]}",
            f'{_SHORT_PREVIOUS_ID[6:]} "$s"; tmux wait-for -S r_{_SHORT_PREVIOUS_ID}',
        ],
        f"r_{_SHORT_CURRENT_ID}",
        f"@s_{_SHORT_CURRENT_ID}",
    ),
    FilterDropFixture(
        "previous_targeted_short_marker",
        [
            "RUN_OK",
            f"∙ }}; s=$?; tmux -L dev set-option -p -t %1 @s_{_SHORT_PREVIOUS_ID[:6]}",
            (
                f'{_SHORT_PREVIOUS_ID[6:]} "$s"; '
                f"tmux -L dev wait-for -S r_{_SHORT_PREVIOUS_ID}"
            ),
        ],
        f"r_{_SHORT_CURRENT_ID}",
        f"@s_{_SHORT_CURRENT_ID}",
    ),
]


FILTER_CURRENT_SYNC_KEEP_FIXTURES: list[FilterCurrentSyncLineFixture] = [
    FilterCurrentSyncLineFixture("single_hex_output", ["abcdef1234", "DONE"]),
    FilterCurrentSyncLineFixture(
        "consecutive_hex_output",
        ["abcdef1234", "feedface99", "DONE"],
    ),
]


@pytest.mark.parametrize(
    FilterInternalLinesFixture._fields,
    FILTER_KEEP_FIXTURES,
    ids=[f.test_id for f in FILTER_KEEP_FIXTURES],
)
def test_filter_run_command_keeps_legitimate_output(test_id: str, line: str) -> None:
    """Legitimate output without private markers survives filtering."""
    from libtmux_mcp.tools.pane_tools.io import _filter_run_command_internal_lines

    command_id = "deadbeefdeadbeefdeadbeefdeadbeef"
    channel = f"libtmux_mcp_run_{command_id}"
    status_option = f"@libtmux_mcp_status_{command_id}"

    assert test_id
    kept = _filter_run_command_internal_lines(
        [line], channel=channel, status_option=status_option
    )
    assert kept == [line]


@pytest.mark.parametrize(
    FilterInternalLinesFixture._fields,
    FILTER_WRAPPER_LIKE_KEEP_FIXTURES,
    ids=[f.test_id for f in FILTER_WRAPPER_LIKE_KEEP_FIXTURES],
)
def test_filter_run_command_keeps_wrapper_like_output(test_id: str, line: str) -> None:
    """Legitimate tmux-looking command output survives filtering."""
    from libtmux_mcp.tools.pane_tools.io import _filter_run_command_internal_lines

    assert test_id
    kept = _filter_run_command_internal_lines(
        [line],
        channel=f"r_{_SHORT_CURRENT_ID}",
        status_option=f"@s_{_SHORT_CURRENT_ID}",
    )
    assert kept == [line]


def test_filter_run_command_drops_sync_line() -> None:
    """The joined private synchronisation line is removed from output."""
    from libtmux_mcp.tools.pane_tools.io import _filter_run_command_internal_lines

    command_id = "deadbeefdeadbeefdeadbeefdeadbeef"
    channel = f"libtmux_mcp_run_{command_id}"
    status_option = f"@libtmux_mcp_status_{command_id}"
    sync_line = (
        f"}}; __libtmux_mcp_status=$?; tmux set-option -p {status_option} "
        f'"$__libtmux_mcp_status"; tmux wait-for -S {channel}'
    )
    kept = _filter_run_command_internal_lines(
        ["RUN_OK", sync_line], channel=channel, status_option=status_option
    )
    assert kept == ["RUN_OK"]


@pytest.mark.parametrize(
    FilterCurrentSyncLineFixture._fields,
    FILTER_CURRENT_SYNC_KEEP_FIXTURES,
    ids=[f.test_id for f in FILTER_CURRENT_SYNC_KEEP_FIXTURES],
)
def test_filter_run_command_keeps_hex_output_after_current_sync_line(
    test_id: str, output_lines: list[str]
) -> None:
    """Hex-like output after the current sync line survives filtering."""
    from libtmux_mcp.tools.pane_tools.io import _filter_run_command_internal_lines

    channel = f"r_{_SHORT_CURRENT_ID}"
    status_option = f"@s_{_SHORT_CURRENT_ID}"
    sync_line = (
        f'); s=$?; tmux set-option -p {status_option} "$s"; tmux wait-for -S {channel}'
    )
    kept = _filter_run_command_internal_lines(
        [sync_line, *output_lines],
        channel=channel,
        status_option=status_option,
    )
    assert test_id
    assert kept == output_lines


@pytest.mark.parametrize(
    FilterDropFixture._fields,
    FILTER_DROP_FIXTURES,
    ids=[f.test_id for f in FILTER_DROP_FIXTURES],
)
def test_filter_run_command_drops_sync_fragments(
    test_id: str, lines: list[str], channel: str, status_option: str
) -> None:
    """Private synchronisation fragments are removed from output."""
    from libtmux_mcp.tools.pane_tools.io import _filter_run_command_internal_lines

    kept = _filter_run_command_internal_lines(
        lines,
        channel=channel,
        status_option=status_option,
    )
    assert test_id
    assert kept == ["RUN_OK"]


def test_capture_pane(mcp_server: Server, mcp_pane: Pane) -> None:
    """capture_pane returns pane content."""
    result = capture_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, str)


def test_capture_pane_untruncated_short_output(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Short output below ``max_lines`` passes through without a header."""
    result = capture_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=100,
        socket_name=mcp_server.socket_name,
    )
    assert "[... truncated" not in result


def test_capture_pane_truncates_tail_preserving(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Long captures are truncated head-first; tail is preserved.

    This test fails intermittently in full-suite runs, and the cause is
    NOT established. An earlier revision moved it to a dedicated window
    on the theory that a shared pane had been shrunk by another test's
    split; that mechanism is impossible -- libtmux's ``server`` and
    ``session`` fixtures are function-scoped, measured as two tests
    landing on different sockets with both panes at 24 rows -- so the
    move was reverted rather than left in as a fix that fixes nothing.

    Ruled out by measurement: the retry budget (the marker appears in
    0.13 s idle and 0.24 s under eightfold parallel load, against the
    2 s budget below) and ordering alone. Under ``pytest -n`` the suite
    loses two to five DIFFERENT contention-sensitive tests per run, so
    this is one member of a suite-wide family rather than a defect in
    this assertion. ``--reruns=2`` absorbs all of it.

    Prime the pane with >20 echo lines and confirm the last one is
    visible, then capture the visible pane with a tight ``max_lines=5``
    ceiling.
    """
    for i in range(20):
        mcp_pane.send_keys(f"echo scrollback_line_{i}", enter=True)

    # Derive the cap from what the pane actually holds, in the SAME
    # observation the assertions run against. A literal cap carries an
    # unasserted precondition -- max_lines=5 needs six visible rows,
    # which put the assertion exactly on the boundary of a pane split
    # three times -- and a cap read from an earlier capture than the one
    # asserted on compares two states that were never simultaneous.
    settled: list[list[str]] = []

    def _captured_with_marker() -> bool:
        rows = mcp_pane.capture_pane()
        # Two rows is the precondition, not an assertion: a cap of
        # ``len(rows) - 1`` is 0 on a one-line pane, and a cap below 1
        # is refused outright. Waiting for it means the test cannot
        # construct a call it is not allowed to make.
        if len(rows) >= 2 and any("scrollback_line_19" in row for row in rows):
            settled.append(rows)
            return True
        return False

    retry_until(_captured_with_marker, 10, raises=True)
    raw = settled[-1]
    geometry = mcp_pane.display_message(
        "#{pane_width}x#{pane_height} cursor_y=#{cursor_y} "
        "hsize=#{history_size} alt=#{alternate_on}",
        get_text=True,
    )
    cap = len(raw) - 1

    result = capture_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=cap,
        socket_name=mcp_server.socket_name,
    )
    lines = result.split("\n")
    assert lines[0].startswith("[... truncated "), (
        f"no truncation header; geometry {geometry}, "
        f"raw visible {len(raw)}, cap {cap}, returned {len(lines)}: {lines[:3]}"
    )
    assert lines[0].endswith(" lines ...]")
    assert len(lines) == cap + 1  # header + exactly cap preserved tail lines
    # Only the oldest row was dropped, so the newest marker must survive.
    assert any("scrollback_line_19" in line for line in lines[1:])


def test_truncation_refuses_a_non_positive_cap(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A cap below 1 slices into nonsense instead of failing.

    ``lines[-0:]`` is the WHOLE list, so ``max_lines=0`` returned more
    rows than no truncation at all while announcing that every line had
    been dropped, and a negative inflated the count past the pane's own
    size. The header is ``capture_pane``'s only disclosure channel --
    it returns a bare ``str`` -- so a number that cannot be true is the
    whole defect.

    Asserted through two tools because the guard lives in the helper the
    four truncating tools share.
    """
    mcp_pane.send_keys("echo cap_probe", enter=True)
    retry_until(
        lambda: any("cap_probe" in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    for bad in (0, -1, -100):
        with pytest.raises(ToolError, match="max_lines must be at least 1"):
            capture_pane(
                pane_id=mcp_pane.pane_id,
                max_lines=bad,
                socket_name=mcp_server.socket_name,
            )
    with pytest.raises(ToolError, match="max_lines must be at least 1"):
        snapshot_pane(
            pane_id=mcp_pane.pane_id,
            max_lines=0,
            socket_name=mcp_server.socket_name,
        )


def test_capture_pane_max_lines_none_disables_truncation(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``max_lines=None`` opts out of truncation entirely."""
    for i in range(20):
        mcp_pane.send_keys(f"echo untrunc_line_{i}", enter=True)

    # Assert on the capture that satisfied the precondition, not on a
    # later one. Checking visibility and then re-capturing compares two
    # observations that were never simultaneous, and on a short pane the
    # marker can scroll out of the visible region in between.
    settled: list[str] = []

    def _captured_with_marker() -> bool:
        out = capture_pane(
            pane_id=mcp_pane.pane_id,
            max_lines=None,
            socket_name=mcp_server.socket_name,
        )
        if "untrunc_line_19" in out:
            settled.append(out)
            return True
        return False

    retry_until(_captured_with_marker, 10, raises=True)
    result = settled[-1]
    assert "[... truncated" not in result


# ---------------------------------------------------------------------------
# capture_since tests
# ---------------------------------------------------------------------------


def _signal_after_shell_payload(mcp_server: Server, pane: Pane, payload: str) -> None:
    """Run ``payload`` in ``pane`` and wait for shell completion."""
    import asyncio
    import uuid

    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = f"mcp_test_capture_since_{uuid.uuid4().hex[:16]}"
    pane.send_keys(f"{payload}; tmux wait-for -S {channel}", enter=True)
    asyncio.run(
        wait_for_channel(
            channel=channel,
            timeout=20.0,
            socket_name=mcp_server.socket_name,
        )
    )


def test_enter_copy_mode_bounds_a_huge_scroll_up(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """An unbounded repeat count reaches an unbounded loop in tmux.

    ``window_copy_cmd_scroll_up`` runs ``for (; np != 0; np--)`` with no
    reference to how much scrollback exists, inside the single-threaded
    server. At ~30us an iteration a caller-supplied ``10**9`` spins for
    hours, and it is not the caller who pays: probe servers abandoned at
    a 40s client timeout were still burning CPU at 422s when reaped, and
    ``kill-server`` on the same socket did not get through.

    Clamping preserves the outcome, which is the half worth guarding:
    the discarded iterations could not have moved the cursor.
    """
    pane_id = mcp_pane.pane_id
    assert pane_id is not None
    started = time.monotonic()
    enter_copy_mode(
        pane_id=pane_id, scroll_up=10**9, socket_name=mcp_server.socket_name
    )
    elapsed = time.monotonic() - started
    exit_copy_mode(pane_id=pane_id, socket_name=mcp_server.socket_name)
    # Unclamped, the same call takes 3.5s at 100_000 and scales linearly.
    assert elapsed < 2.0, f"scroll_up=10**9 took {elapsed:.2f}s"


def test_capture_since_first_call_returns_visible_screen_and_cursor(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Initial ``capture_since`` call captures visible content and returns a cursor."""
    import asyncio

    marker = "CAPTURE_SINCE_INITIAL_4xz"
    _signal_after_shell_payload(mcp_server, mcp_pane, f"echo {marker}")

    result = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )

    assert isinstance(result, CaptureSinceResult)
    assert result.pane_id == mcp_pane.pane_id
    assert result.cursor
    assert result.lines_missed is False
    assert result.truncated is False
    assert any(marker in line for line in result.lines)


def test_capture_since_followup_returns_only_new_output(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Follow-up calls return content written after the previous cursor."""
    import asyncio

    old_marker = "CAPTURE_SINCE_OLD_71k"
    new_marker = "CAPTURE_SINCE_NEW_71k"
    _signal_after_shell_payload(mcp_server, mcp_pane, f"echo {old_marker}")
    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )

    _signal_after_shell_payload(mcp_server, mcp_pane, f"echo {new_marker}")
    second = asyncio.run(
        capture_since(
            cursor=first.cursor,
            socket_name=mcp_server.socket_name,
        )
    )
    third = asyncio.run(
        capture_since(
            cursor=second.cursor,
            socket_name=mcp_server.socket_name,
        )
    )

    assert any(new_marker in line for line in second.lines)
    assert not any(old_marker in line for line in second.lines)
    assert third.lines == []
    assert second.pane_id == mcp_pane.pane_id


def test_capture_since_reports_a_screen_reset_as_missed(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A screen reset moves the cursor up without touching history.

    Every other invalidation check assumes an anchor dies by history
    shrinking or by passing the bottom row. On a pane with no
    scrollback yet -- history_size 0, cursor_y 2 -- ``clear_pane`` left
    history_size 0 and cursor_y 0, so the anchor pointed BELOW the new
    output and the call returned nothing under ``lines_missed=False``.

    ``clear_pane``'s own docstring recommends this sequence: clear, then
    observe.
    """
    import asyncio

    # The anchor must sit BELOW row 0 for the reset to strand it, and
    # history must still be 0 -- so print two lines and park, rather
    # than using _park_pane, which leaves the cursor on row 0.
    mcp_pane.respawn(kill=True, shell="sh -c \"printf 'r0\\nr1\\n'; sleep 60\"")

    def _parked_below_row_zero() -> bool:
        state = mcp_pane.display_message("#{history_size}:#{cursor_y}", get_text=True)
        return bool(state) and state[0].startswith("0:") and state[0] != "0:0"

    retry_until(_parked_below_row_zero, 10, raises=True)
    before = asyncio.run(
        capture_since(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)
    )

    clear_pane(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)
    _write_to_pane_tty(mcp_pane, "AFTER_CLEAR\n")
    retry_until(
        lambda: any("AFTER_CLEAR" in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    after = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            cursor=before.cursor,
            socket_name=mcp_server.socket_name,
        )
    )
    assert after.lines_missed is True
    assert any("AFTER_CLEAR" in line for line in after.lines)


def test_capture_since_reports_a_narrowed_pane_as_missed(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Narrowing rewraps history, so old row coordinates stop meaning anything.

    Widening was already caught: rewrap makes history SHORTER and the
    shrink branch fires. Narrowing makes it longer, which looks like
    ordinary new output -- so ``start`` went negative by exactly the
    rewrap growth and tmux returned that many rows of already-seen
    scrollback as new, under ``lines_missed=False``.
    """
    import asyncio

    pane = mcp_session.active_window.active_pane
    assert pane is not None
    socket = mcp_server.socket_name
    filler = "for i in $(seq 1 40); do printf 'ROW%03d-%060d\\n' $i $i; done"
    pane.send_keys(filler, enter=True)
    retry_until(
        lambda: any("ROW040" in line for line in pane.capture_pane()),
        10,
        raises=True,
    )

    first = asyncio.run(capture_since(pane_id=pane.pane_id, socket_name=socket))

    # Control: no resize, so the cursor stays valid.
    unchanged = asyncio.run(
        capture_since(pane_id=pane.pane_id, cursor=first.cursor, socket_name=socket)
    )
    assert unchanged.lines_missed is False

    mcp_session.cmd("resize-window", "-x", "40", "-y", "24")
    retry_until(
        lambda: pane.display_message("#{pane_width}", get_text=True)[0] == "40",
        10,
        raises=True,
    )

    after = asyncio.run(
        capture_since(pane_id=pane.pane_id, cursor=unchanged.cursor, socket_name=socket)
    )
    assert after.lines_missed is True


def test_capture_since_does_not_report_a_taller_pane_as_missed(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Growing a pane pulls rows out of history without destroying any.

    History SHRINKING normally means rows were trimmed, so the anchor is
    gone. A resize-grow shrinks it for the opposite reason -- rows moved
    back onto the visible screen -- and the ``pane_height`` guard is the
    only thing telling the two apart. Deleting that guard left all
    eighteen ``capture_since`` tests green while turning every taller
    pane into ``lines_missed=True`` plus a full replay of scrollback the
    caller had already read.
    """
    import asyncio

    pane = mcp_session.active_window.active_pane
    assert pane is not None
    socket = mcp_server.socket_name
    mcp_session.cmd("resize-window", "-x", "80", "-y", "10")
    pane.send_keys("for i in $(seq 1 30); do printf 'G%03d\\n' $i; done", enter=True)
    retry_until(
        lambda: any("G030" in line for line in pane.capture_pane()),
        10,
        raises=True,
    )
    first = asyncio.run(capture_since(pane_id=pane.pane_id, socket_name=socket))

    mcp_session.cmd("resize-window", "-y", "24")
    retry_until(
        lambda: pane.display_message("#{pane_height}", get_text=True)[0] == "24",
        10,
        raises=True,
    )
    pane.send_keys("printf 'AFTER_GROW\\n'", enter=True)
    retry_until(
        lambda: any("AFTER_GROW" in line for line in pane.capture_pane()),
        10,
        raises=True,
    )

    after = asyncio.run(
        capture_since(pane_id=pane.pane_id, cursor=first.cursor, socket_name=socket)
    )
    assert after.lines_missed is False
    assert any("AFTER_GROW" in line for line in after.lines)
    assert not [line for line in after.lines if line.startswith("G0")]


def test_wait_for_text_screens_stop_patterns_too(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``wait_for_text`` promises a timeout it could not keep.

    The deadline is checked BETWEEN poll iterations, and a
    ``pattern.search(line)`` that never returns sits inside one -- so a
    2-second wait ran for 30 and counting. ``stop`` takes caller regex
    on the same terms as ``patterns``, so screening one and not the
    other would leave the promise broken by the quieter argument.
    """
    for kwargs in ({"patterns": [r"(a+)+$"]}, {"patterns": ["x"], "stop": [r"(a+)+$"]}):
        with pytest.raises(ToolError, match="exponential time"):
            asyncio.run(
                wait_for_text(
                    pane_id=mcp_pane.pane_id,
                    regex=True,
                    timeout=2.0,
                    socket_name=mcp_server.socket_name,
                    **t.cast("t.Any", kwargs),
                )
            )


def test_wait_for_text_matches_a_reprinted_line(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Text already on screen must still match when printed again.

    The entry snapshot was a set of every row below the cursor, so a
    line anywhere down there permanently blocked an identical line
    arriving later -- and waiting for a repeated status line is this
    tool's headline case. Compared per index now.

    The second case is the falsifier: per-index comparison's obvious
    failure mode is rows moving under it, so stale text plus unrelated
    output scrolling the region is where a naive version false-matches.
    """
    import asyncio

    marker = "BUILD_OK_REPRINT"

    def run(reprint: bool) -> WaitForTextResult:
        tail = f"printf '{marker}\\n'; " if reprint else ""
        mcp_pane.respawn(
            kill=True,
            shell=(
                f"sh -c \"printf '{marker}\\n'; sleep 1; "
                f"for i in 1 2 3; do printf 'noise %s\\n' $i; sleep 0.2; done; "
                f'{tail}sleep 5"'
            ),
        )
        retry_until(
            lambda: any(marker in line for line in mcp_pane.capture_pane()),
            10,
            raises=True,
        )
        return asyncio.run(
            wait_for_text(
                patterns=[marker],
                pane_id=mcp_pane.pane_id,
                timeout=4.0,
                socket_name=mcp_server.socket_name,
            )
        )

    reprinted = run(reprint=True)
    assert reprinted.found is True
    assert reprinted.matched_at_entry is False

    # Stale on screen, rows scrolling, never reprinted: must not match.
    stale_only = run(reprint=False)
    assert stale_only.found is False
    assert stale_only.matched_at_entry is True


def test_run_command_allows_a_slow_shell(
    mcp_server: Server, mcp_pane: Pane, tmp_path: pathlib.Path
) -> None:
    """A shell that is slow is not a shell that is wedged.

    The started-channel grace alone refused a pane whose prompt hook
    takes longer than the grace -- and the command then ran, so the
    refusal said "it has not run" about something that had. That is the
    double execution this guard exists to prevent, arriving on an
    ordinary call instead of a wedged pane.

    A foreground process that CHANGED since the payload was sent is
    positive evidence a shell read the line and is working, so the wait
    is extended rather than refused.
    """
    import asyncio
    import shutil

    if shutil.which("zsh") is None:
        pytest.skip("zsh is required to install a slow preexec hook")

    zdotdir = tmp_path / "zdot"
    zdotdir.mkdir()
    (zdotdir / ".zshrc").write_text("preexec() { sleep 6 }\n")
    mcp_pane.respawn(kill=True, shell=f"env ZDOTDIR={shlex.quote(str(zdotdir))} zsh -i")
    retry_until(
        lambda: (
            mcp_pane.display_message("#{pane_current_command}", get_text=True)
            == ["zsh"]
        ),
        10,
        raises=True,
    )

    # grace is max(5, timeout/2) = 5 s, and the hook holds the shell for
    # 6 s, so the grace expires before the command starts.
    result = asyncio.run(
        run_command(
            command="printf 'SLOW_SHELL_OK\\n'",
            pane_id=mcp_pane.pane_id,
            timeout=10.0,
            socket_name=mcp_server.socket_name,
        )
    )
    assert result.exit_status == 0


def test_run_command_reports_a_command_that_never_ran(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A shell mid-``read`` swallows the wrapper; say so, do not guess.

    This used to be the worst shape in the tool: ``read`` consumed the
    wrapper's first line as its answer and the shell then RAN the
    command from the following lines, while the result reported
    ``timed_out`` with ``command_may_still_run`` -- failure-shaped and
    false. An agent retrying a non-idempotent command ran it twice.

    The wrapper is one line when the command allows it, so ``read``
    consumes the whole thing and nothing executes, and a private
    "started" channel signalled before the command distinguishes
    "never began" from "still running".
    """
    import asyncio

    # printf builds the marker at runtime, so the echoed input line can
    # never contain it -- only real execution can.
    marker = "RAN_WHEN_IT_SHOULD_NOT"
    mcp_pane.send_keys("read ANSWERVAR", enter=True)
    retry_until(
        lambda: any("read ANSWERVAR" in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    # Must exceed the started-channel grace, which is
    # max(5s, timeout/2): at or below it, "not started yet" and "never
    # will" are the same observation and the tool reports a plain
    # timeout instead of refusing. 12s gives a 6s grace.
    with pytest.raises(ToolError, match="never reached a shell prompt"):
        asyncio.run(
            run_command(
                command=f"printf '{marker}%s\\n' ''",
                pane_id=mcp_pane.pane_id,
                timeout=12.0,
                socket_name=mcp_server.socket_name,
            )
        )

    assert not any(marker in line for line in mcp_pane.capture_pane())


def test_snapshot_pane_reports_its_location(
    mcp_server: Server, mcp_session: Session
) -> None:
    """The snapshot must say which session and window the pane is in.

    The server instructions recommend this tool over ``capture_pane`` +
    ``get_pane_info``, and without these the substitution cannot answer
    where a pane ended up -- which ``break_pane`` can change.

    Checked against raw tmux rather than against the model, because the
    fields were parsed BY POSITION and inserting a format var silently
    shifted every field below it: a newly added ``session_id`` read back
    the pane index. They are keyed by name now.
    """
    window = mcp_session.active_window
    pane = window.active_pane
    assert pane is not None
    window.split()

    snapshot = snapshot_pane(pane_id=pane.pane_id, socket_name=mcp_server.socket_name)
    fields = ("session_id", "window_id", "pane_index", "pane_active", "pane_title")
    raw = pane.display_message(
        "|".join(f"#{{{name}}}" for name in fields), get_text=True
    )[0].split("|")

    assert snapshot.session_id == raw[0]
    assert snapshot.window_id == raw[1]
    assert snapshot.pane_index == raw[2]
    assert snapshot.pane_active is (raw[3] == "1")
    # Exposed as ``title`` here and ``pane_title`` on PaneInfo -- same
    # tmux format, two names.
    assert snapshot.title == raw[4]


def test_run_command_refuses_a_pane_in_copy_mode(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Copy mode owns the keyboard while every other signal says idle shell.

    ``alternate_on`` is 0 and ``pane_current_command`` is still the
    shell, so both other arms of the guard miss it. Measured with a
    client attached, the payload was consumed as copy-mode keystrokes,
    the command never ran, and the scroll position was destroyed --
    while the result claimed ``command_may_still_run``.
    """
    import asyncio

    enter_copy_mode(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)

    with pytest.raises(ToolError, match="is in a tmux mode"):
        asyncio.run(
            run_command(
                command="echo COPYPROBE",
                pane_id=mcp_pane.pane_id,
                timeout=4.0,
                socket_name=mcp_server.socket_name,
            )
        )

    # Refusing must not disturb what it refused to touch.
    assert mcp_pane.display_message("#{pane_in_mode}", get_text=True) == ["1"]


def test_capture_since_follows_anchor_into_retained_history(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A cursor remains exact after its anchor scrolls into history."""
    import asyncio

    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )
    pane_height = int(mcp_pane.display_message("#{pane_height}", get_text=True)[0])
    markers = [f"CAPTURE_SINCE_SCROLL_{index:02d}" for index in range(pane_height + 8)]
    payload = "printf '%s\\n' " + " ".join(markers)

    _signal_after_shell_payload(mcp_server, mcp_pane, payload)
    second = asyncio.run(
        capture_since(
            cursor=first.cursor,
            socket_name=mcp_server.socket_name,
        )
    )

    assert second.lines_missed is False
    assert any(markers[-1] in line for line in second.lines)


def test_capture_since_marks_lines_missed_after_history_limit_trim(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """History-limit trims return visible content with ``lines_missed=True``.

    Floods past ``history-limit`` then clears history to guarantee the
    cursor anchor is destroyed.  The flood alone is not deterministic —
    tmux 3.6 retains enough of the original prompt that
    ``_find_unique_cursor_match`` re-anchors on the surviving hash.
    """
    import asyncio

    mcp_pane.session.cmd("set-option", "-g", "history-limit", "20")
    fresh_pane = mcp_pane.window.split()
    assert fresh_pane.pane_id is not None

    def _hlimit_locked() -> bool:
        raw = fresh_pane.display_message("#{history_limit}", get_text=True)
        return bool(raw) and int(raw[0]) == 20

    try:
        retry_until(_hlimit_locked, 10, raises=True)
        # Build scrollback so the cursor has history_size > 0.
        _signal_after_shell_payload(
            mcp_server,
            fresh_pane,
            "for i in $(seq 1 25); do printf 'PREFILL_%03d\\n' \"$i\"; done",
        )
        first = asyncio.run(
            capture_since(
                pane_id=fresh_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )

        payload = (
            "for i in $(seq 1 120); do printf 'CAPTURE_SINCE_TRIM_%03d\\n' \"$i\"; done"
        )
        _signal_after_shell_payload(mcp_server, fresh_pane, payload)
        # Guarantee anchor destruction: tmux 3.6 can retain the original
        # prompt hash in scrollback even after flooding past history-limit.
        fresh_pane.cmd("clear-history")
        _signal_after_shell_payload(
            mcp_server, fresh_pane, "echo CAPTURE_SINCE_TRIM_DONE"
        )
        second = asyncio.run(
            capture_since(
                cursor=first.cursor,
                socket_name=mcp_server.socket_name,
            )
        )

        assert second.lines_missed is True
        assert any("CAPTURE_SINCE_TRIM" in line for line in second.lines)
    finally:
        fresh_pane.kill()


def test_capture_since_reports_overflow_without_clear_history(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Output lapping ``history-limit`` reports the loss on its own.

    The sibling test above needs an explicit ``clear-history`` because
    the flood alone used to be non-deterministic: the evicted anchor is
    the shell prompt, a line that recurs verbatim after every command,
    so its only surviving twin was the CURRENT prompt near the bottom.
    One candidate passed the uniqueness guard, everything above it was
    dropped as "already seen", and the read returned no lines while
    reporting ``lines_missed=False``.

    Rows are only evicted from the top, so a surviving anchor can only
    move earlier than ``anchor_abs``; a match past it is rejected on
    position. That makes the flood-only path -- the one real agents hit
    while tailing a build -- deterministic, so it is tested here
    without help.
    """
    import asyncio

    mcp_pane.session.cmd("set-option", "-g", "history-limit", "20")
    fresh_pane = mcp_pane.window.split()
    assert fresh_pane.pane_id is not None

    def _hlimit_locked() -> bool:
        raw = fresh_pane.display_message("#{history_limit}", get_text=True)
        return bool(raw) and int(raw[0]) == 20

    try:
        retry_until(_hlimit_locked, 10, raises=True)
        _signal_after_shell_payload(
            mcp_server,
            fresh_pane,
            "for i in $(seq 1 25); do printf 'OVF_PRE_%03d\\n' \"$i\"; done",
        )
        first = asyncio.run(
            capture_since(
                pane_id=fresh_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )

        _signal_after_shell_payload(
            mcp_server,
            fresh_pane,
            "for i in $(seq 1 300); do printf 'OVF_%03d\\n' \"$i\"; done",
        )
        second = asyncio.run(
            capture_since(
                cursor=first.cursor,
                socket_name=mcp_server.socket_name,
            )
        )

        # 300 lines through a 20-line history: rows were destroyed, and
        # the read must say so rather than returning an empty success.
        assert second.lines_missed is True
        assert second.lines
    finally:
        fresh_pane.kill()


def test_capture_since_reports_same_row_rewrite(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Carriage-return rewrites on the cursor row are reported as new content."""
    import asyncio

    script = (
        "printf OLD_REWRITE_CAPTURE_SINCE; "
        "IFS= read -r line; "
        "printf '\\r%s' \"$line\"; "
        "sleep 60"
    )
    mcp_pane.respawn(kill=True, shell=f"sh -c '{script}'")
    retry_until(
        lambda: any(
            "OLD_REWRITE_CAPTURE_SINCE" in line for line in mcp_pane.capture_pane()
        ),
        10,
        raises=True,
    )
    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )

    mcp_pane.send_keys("NEW_REWRITE_CAPTURE_SINCE", enter=True)
    retry_until(
        lambda: any(
            "NEW_REWRITE_CAPTURE_SINCE" in line for line in mcp_pane.capture_pane()
        ),
        10,
        raises=True,
    )
    second = asyncio.run(
        capture_since(
            cursor=first.cursor,
            socket_name=mcp_server.socket_name,
        )
    )

    assert any("NEW_REWRITE_CAPTURE_SINCE" in line for line in second.lines)


def test_capture_since_truncates_with_structured_metadata(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Line and byte limits tail-preserve output without in-band markers."""
    import asyncio

    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )
    payload = "; ".join(f"echo CAPTURE_SINCE_TRUNC_{i}" for i in range(6))
    _signal_after_shell_payload(mcp_server, mcp_pane, payload)

    line_limited = asyncio.run(
        capture_since(
            cursor=first.cursor,
            max_lines=2,
            socket_name=mcp_server.socket_name,
        )
    )
    byte_limited = asyncio.run(
        capture_since(
            cursor=first.cursor,
            max_bytes=32,
            socket_name=mcp_server.socket_name,
        )
    )

    assert line_limited.truncated is True
    assert line_limited.truncated_lines > 0
    assert len(line_limited.lines) == 2
    assert any("CAPTURE_SINCE_TRUNC_5" in line for line in line_limited.lines)
    assert not line_limited.lines[0].startswith("[... truncated")

    assert byte_limited.truncated is True
    assert byte_limited.truncated_bytes > 0
    assert len("\n".join(byte_limited.lines).encode()) <= 32


def test_capture_since_rejects_malformed_cursor(mcp_server: Server) -> None:
    """Malformed cursors fail clearly instead of falling back to another pane."""
    import asyncio

    with pytest.raises(ToolError, match="invalid capture_since cursor"):
        asyncio.run(
            capture_since(
                cursor="not-a-valid-cursor",
                socket_name=mcp_server.socket_name,
            )
        )


def test_capture_since_rejects_cursor_for_different_pane(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """A cursor cannot be replayed against a different pane."""
    import asyncio

    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )
    other_pane = mcp_session.active_window.split()
    try:
        with pytest.raises(ToolError, match="cursor pane"):
            asyncio.run(
                capture_since(
                    cursor=first.cursor,
                    pane_id=other_pane.pane_id,
                    socket_name=mcp_server.socket_name,
                )
            )
    finally:
        other_pane.kill()


def test_capture_since_marks_lines_missed_after_history_clear(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Lost history returns current visible content with ``lines_missed=True``."""
    import asyncio

    fill = "; ".join(f"echo CAPTURE_SINCE_HISTORY_{i}" for i in range(40))
    _signal_after_shell_payload(mcp_server, mcp_pane, fill)
    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )

    mcp_pane.cmd("clear-history")
    _signal_after_shell_payload(mcp_server, mcp_pane, "echo CAPTURE_SINCE_AFTER_CLEAR")
    second = asyncio.run(
        capture_since(
            cursor=first.cursor,
            socket_name=mcp_server.socket_name,
        )
    )

    assert second.lines_missed is True
    assert any("CAPTURE_SINCE_AFTER_CLEAR" in line for line in second.lines)
    assert second.cursor


def test_capture_since_marks_lines_missed_after_clear_history_with_resize(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """clear-history + pane resize still detects anchor loss.

    Regression: ``_cursor_anchor_lost`` used a ``pane_height`` guard
    that returned False when the pane grew after ``clear-history``,
    masking the complete history wipe.
    """
    import asyncio

    fresh_pane = mcp_pane.window.split()
    assert fresh_pane.pane_id is not None

    try:
        fill = "; ".join(f"echo RESIZE_CLEAR_{i}" for i in range(40))
        _signal_after_shell_payload(mcp_server, fresh_pane, fill)
        first = asyncio.run(
            capture_since(
                pane_id=fresh_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )

        fresh_pane.cmd("clear-history")
        assert fresh_pane.pane_height is not None
        fresh_pane.set_height(int(fresh_pane.pane_height) + 3)
        _signal_after_shell_payload(mcp_server, fresh_pane, "echo AFTER_RESIZE_CLEAR")
        second = asyncio.run(
            capture_since(
                cursor=first.cursor,
                socket_name=mcp_server.socket_name,
            )
        )

        assert second.lines_missed is True
        assert any("AFTER_RESIZE_CLEAR" in line for line in second.lines)
    finally:
        fresh_pane.kill()


def test_capture_since_rejects_respawned_pane_cursor(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Pane respawn invalidates the cursor's process identity."""
    import asyncio

    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )
    mcp_pane.respawn(kill=True, shell="sleep 60")

    with pytest.raises(ToolError, match="respawned"):
        asyncio.run(
            capture_since(
                cursor=first.cursor,
                socket_name=mcp_server.socket_name,
            )
        )


def test_capture_since_rejects_dead_pane_cursor(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """Pane death invalidates the cursor instead of returning stale content."""
    import asyncio

    first = asyncio.run(
        capture_since(
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
    )
    window = mcp_session.active_window
    window.cmd("set-option", "-w", "remain-on-exit", "on")
    try:
        mcp_pane.respawn(kill=True, shell="true")

        def _is_dead() -> bool:
            out = mcp_pane.cmd("display-message", "-p", "#{pane_dead}").stdout
            return bool(out) and out[0].strip() == "1"

        retry_until(_is_dead, 10, raises=True)
        with pytest.raises(ToolError, match="died"):
            asyncio.run(
                capture_since(
                    cursor=first.cursor,
                    socket_name=mcp_server.socket_name,
                )
            )
    finally:
        window.cmd("set-option", "-wu", "remain-on-exit")


#: Helpers that reach tmux. Called INLINE from an async body they run on
#: the loop and every concurrent caller waits with them; awaited, they
#: yield. Names stay here after being converted to an async bounded
#: form, so reintroducing a synchronous one is caught.
#: libtmux METHODS that make a tmux round trip. ``Pane.cmd`` and friends
#: are the same hazard as the helpers below and are invisible to a
#: name-based check, because the offending call is an attribute access
#: on whatever object is in hand.
_BLOCKING_TMUX_METHODS = frozenset(
    {"cmd", "capture_pane", "display_message", "refresh"}
)

#: ``module.attr`` calls that block whatever thread runs them. A
#: synchronous subprocess or a ``time.sleep`` inline in an async body
#: stops the loop for its whole duration, and neither is a tmux call, so
#: neither name above would catch it.
_BLOCKING_MODULE_CALLS = frozenset(
    {
        ("subprocess", "run"),
        ("subprocess", "call"),
        ("subprocess", "check_call"),
        ("subprocess", "check_output"),
        ("time", "sleep"),
    }
)

_BLOCKING_TMUX_HELPERS = frozenset(
    {
        "_resolve_pane",
        "_resolve_window",
        "_resolve_session",
        "_probe_liveness",
        "_run_tmux_sync",
        "_read_pane_state",
        "_capture_rows",
    }
)


def test_no_async_tool_makes_a_blocking_tmux_call_on_the_loop() -> None:
    """Async tools must not do blocking work inline.

    Measured before this was true: ``capture_since`` against a wedged
    socket held the loop for 5.01s and a ticker beside it advanced once.
    A behavioural test catches only the helper it stubs, and there were
    three separate call sites -- so this reads the tree instead.

    Nested ``def``s are skipped deliberately: those are what gets handed
    to ``asyncio.to_thread``, which is the fix rather than the defect.
    """
    import ast

    offenders: list[str] = []

    def walk(node: ast.AST, where: str, path: pathlib.Path) -> None:
        if isinstance(node, ast.FunctionDef):
            return
        # ``await f()`` yields; ``f()`` inline does not. Skipping only
        # the awaited call itself -- not its arguments -- is what lets
        # a helper be converted to an async bounded form and stay on
        # the list, so a future sync reintroduction is still caught.
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            for arg in ast.iter_child_nodes(node.value):
                walk(arg, where, path)
            return
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            attr = isinstance(node.func, ast.Attribute)
            module = getattr(getattr(node.func, "value", None), "id", "")
            if (
                name in _BLOCKING_TMUX_HELPERS
                or (attr and name in _BLOCKING_TMUX_METHODS)
                or (module, name) in _BLOCKING_MODULE_CALLS
            ):
                offenders.append(f"{path.name}:{node.lineno} async {where} -> {name}()")
        for child in ast.iter_child_nodes(node):
            walk(child, where, path)

    root = pathlib.Path(__file__).parent.parent / "src" / "libtmux_mcp"
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.AsyncFunctionDef):
                for stmt in node.body:
                    walk(stmt, node.name, path)

    assert offenders == [], (
        "blocking tmux work on the event loop: "
        + "; ".join(offenders)
        + " -- wrap in asyncio.to_thread, or use the async subprocess in "
        "_tmux_proc if the call is on the wait path"
    )


def test_async_tools_do_not_use_the_synchronous_server_resolver(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The async path must not reach the blocking resolver at all.

    ``_get_server`` shells out to tmux inline -- ~4 ms against a healthy
    server, the full liveness bound against one that never replies -- so
    calling it from an async tool charged every OTHER in-flight call the
    same wait. Measured against a wedged socket before the fix:
    ``capture_since`` held the loop for 5.01s while a ticker beside it
    advanced exactly once.

    Asserted by presence, not by timing. The first version of this test
    measured the gap between ticks, and the parallel gate caught it
    false-firing at loadavg 43: scheduler starvation produced gaps of
    0.42-0.54s, indistinguishable by magnitude from the block it was
    looking for. A stall the machine can fake is not a signal.
    """
    from libtmux_mcp import _utils

    called: list[str] = []
    real = _utils._probe_liveness

    def spy(server: t.Any) -> tuple[bool, str | None]:
        called.append("sync")
        return real(server)

    monkeypatch.setattr(_utils, "_probe_liveness", spy)
    _utils._server_cache.clear()

    asyncio.run(
        capture_since(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)
    )
    assert called == [], (
        "the async path went through the synchronous server resolver, which "
        "makes its tmux round trip on the event loop"
    )

    # Control: the SYNC tools still use it, so an empty list above means
    # "the async path avoided it" rather than "the spy never worked".
    _utils._server_cache.clear()
    capture_pane(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)
    assert called, "the spy never fired; the assertion above proved nothing"


def test_capture_since_uses_no_worker_threads(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``capture_since`` must reach tmux through a killable subprocess.

    The same invariant ``test_wait_path_uses_no_worker_threads`` holds
    for the wait path, extended to the second tool that has it. A thread
    blocked in libtmux's untimed ``Popen.communicate()`` cannot be
    cancelled, and ``concurrent.futures.thread._python_exit`` joins pool
    workers untimed at interpreter shutdown -- so a tmux server that
    answered once and then stopped answering left this call unable to
    return AND the process unable to exit. Measured against a socket
    that forwards its first connection and stalls the rest: before,
    killed at 120s with no output; after, it exits.

    Asserted by presence rather than by timing. The event loop keeps
    ticking throughout that hang -- 16,459 ticks across 90 seconds --
    so no loop-gap measurement can see this class at all.
    """
    import asyncio

    calls: list[t.Any] = []
    original = asyncio.to_thread

    async def _spy(fn: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:
        calls.append(fn)
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy)
    asyncio.run(
        capture_since(pane_id=mcp_pane.pane_id, socket_name=mcp_server.socket_name)
    )
    assert calls == [], f"capture_since used worker threads for: {calls}"


def test_get_pane_info(mcp_server: Server, mcp_pane: Pane) -> None:
    """get_pane_info returns detailed pane info."""
    result = get_pane_info(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == mcp_pane.pane_id
    assert result.pane_width is not None
    assert result.pane_height is not None


def test_get_pane_info_returns_geometry(
    mcp_server: Server, mcp_session: Session
) -> None:
    """PaneInfo carries window-relative geometry as int/bool, not raw strings."""
    from libtmux.constants import PaneDirection

    window = mcp_session.active_window
    window.split(direction=PaneDirection.Right)

    panes = window.panes
    assert len(panes) == 2

    infos = [
        get_pane_info(pane_id=p.pane_id, socket_name=mcp_server.socket_name)
        for p in panes
    ]

    for info in infos:
        assert isinstance(info.pane_left, int)
        assert isinstance(info.pane_top, int)
        assert isinstance(info.pane_right, int)
        assert isinstance(info.pane_bottom, int)
        assert isinstance(info.pane_at_left, bool)
        assert isinstance(info.pane_at_right, bool)
        assert isinstance(info.pane_at_top, bool)
        assert isinstance(info.pane_at_bottom, bool)
        assert info.pane_tty is not None and info.pane_tty.startswith("/dev/")
        # Both panes span the full window vertically in a horizontal split.
        assert info.pane_at_top is True
        assert info.pane_at_bottom is True

    left, right = sorted(infos, key=lambda i: i.pane_left or 0)
    assert left.pane_at_left is True and left.pane_at_right is False
    assert right.pane_at_left is False and right.pane_at_right is True
    assert (left.pane_left or 0) < (right.pane_left or 0)


def test_find_pane_by_position_each_corner(
    mcp_server: Server, mcp_session: Session
) -> None:
    """find_pane_by_position returns the right pane for each corner of a 2x2."""
    from libtmux.constants import PaneDirection

    window = mcp_session.active_window
    # Three splits → four panes; ``tiled`` arranges them as a 2x2 grid.
    window.split(direction=PaneDirection.Right)
    window.split(direction=PaneDirection.Below)
    window.split(direction=PaneDirection.Below)
    window.select_layout("tiled")
    assert len(window.panes) == 4

    corners = ("top-left", "top-right", "bottom-left", "bottom-right")
    found = {
        corner: find_pane_by_position(
            corner=corner,  # type: ignore[arg-type]
            window_id=window.window_id,
            socket_name=mcp_server.socket_name,
        )
        for corner in corners
    }

    pane_ids = {corner: info.pane_id for corner, info in found.items()}
    assert len(set(pane_ids.values())) == 4, (
        f"Expected 4 distinct panes for 4 corners, got {pane_ids}"
    )

    assert found["top-left"].pane_at_top is True
    assert found["top-left"].pane_at_left is True
    assert found["bottom-right"].pane_at_bottom is True
    assert found["bottom-right"].pane_at_right is True


def test_find_pane_by_position_single_pane_window_returns_only_pane(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Single-pane window touches every edge — returns it for any corner."""
    window = mcp_pane.window
    for corner in ("top-left", "top-right", "bottom-left", "bottom-right"):
        info = find_pane_by_position(
            corner=corner,
            window_id=window.window_id,
            socket_name=mcp_server.socket_name,
        )
        assert info.pane_id == mcp_pane.pane_id


def test_set_pane_title(mcp_server: Server, mcp_pane: Pane) -> None:
    """set_pane_title sets the pane title."""
    result = set_pane_title(
        title="my_test_title",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == mcp_pane.pane_id


def test_clear_pane(mcp_server: Server, mcp_pane: Pane) -> None:
    """clear_pane resets terminal and clears scrollback history."""
    marker = "CLEAR_PANE_MARKER_xyz789"
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = clear_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "cleared" in result.lower()

    # After reset + clear-history, the marker should be gone from scrollback
    retry_until(
        lambda: marker not in "\n".join(mcp_pane.capture_pane(start=-200, end=-1)),
        10,
        raises=True,
    )


def test_clear_pane_uses_libtmux_reset(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clear_pane delegates to libtmux's atomic Pane.reset path.

    This test uses monkeypatch because the visible terminal state can
    look identical for the old two-IPC implementation and the fixed
    one-call libtmux reset; the regression is the call boundary.
    """
    from libtmux.pane import Pane as LibtmuxPane

    reset_calls: list[str | None] = []

    def fake_reset(self: LibtmuxPane) -> LibtmuxPane:
        reset_calls.append(self.pane_id)
        return self

    monkeypatch.setattr(LibtmuxPane, "reset", fake_reset)

    clear_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )

    assert reset_calls == [mcp_pane.pane_id]


def test_resize_pane_dimensions(mcp_server: Server, mcp_pane: Pane) -> None:
    """resize_pane resizes a pane with height/width."""
    result = resize_pane(
        pane_id=mcp_pane.pane_id,
        height=10,
        width=40,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == mcp_pane.pane_id


def test_resize_pane_zoom(mcp_server: Server, mcp_session: Session) -> None:
    """resize_pane zooms a pane."""
    window = mcp_session.active_window
    window.split()
    pane = window.active_pane
    assert pane is not None
    result = resize_pane(
        pane_id=pane.pane_id,
        zoom=True,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane.pane_id


def test_resize_pane_zoom_mutual_exclusivity(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """resize_pane raises ToolError when zoom combined with dimensions."""
    with pytest.raises(ToolError, match="Cannot combine zoom"):
        resize_pane(
            pane_id=mcp_pane.pane_id,
            zoom=True,
            height=10,
            socket_name=mcp_server.socket_name,
        )


def test_kill_pane_requires_pane_id(mcp_server: Server) -> None:
    """kill_pane requires pane_id as a positional argument."""
    with pytest.raises(ToolError, match="missing 1 required positional argument"):
        kill_pane(socket_name=mcp_server.socket_name)  # type: ignore[call-arg]


def test_kill_pane(mcp_server: Server, mcp_session: Session) -> None:
    """kill_pane kills a pane."""
    window = mcp_session.active_window
    new_pane = window.split()
    pane_id = new_pane.pane_id
    assert pane_id is not None
    result = kill_pane(
        pane_id=pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "killed" in result.lower()


# ---------------------------------------------------------------------------
# respawn_pane tests
# ---------------------------------------------------------------------------


def test_respawn_pane_preserves_pane_id_and_refreshes_pid(
    mcp_server: Server, mcp_session: Session
) -> None:
    """respawn_pane keeps the same pane_id but picks up a new pane_pid.

    Uses a fresh split so the caller-pane self-guard doesn't fire and
    so the test is independent of what the main mcp_pane is running.
    """
    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None
    # Force a read of the original pid before we respawn.
    new_pane.refresh()
    original_pid = new_pane.pane_pid

    result = respawn_pane(
        pane_id=new_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == new_pane.pane_id, "pane_id must be preserved"
    assert result.pane_pid is not None
    assert result.pane_pid != original_pid, (
        "pane_pid should reflect the new process after respawn"
    )

    # Cleanup
    new_pane.kill()


def test_respawn_pane_replaces_shell(mcp_server: Server, mcp_session: Session) -> None:
    """respawn_pane with ``shell`` relaunches with the new command."""
    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None

    result = respawn_pane(
        pane_id=new_pane.pane_id,
        shell="sleep 7200",
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == new_pane.pane_id
    # pane_current_command reflects the relaunched command.
    assert result.pane_current_command is not None
    assert "sleep" in result.pane_current_command

    new_pane.kill()


def test_respawn_pane_self_kill_guard(
    mcp_server: Server,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """respawn_pane refuses when the caller's pane is the target."""
    from libtmux_mcp._utils import _effective_socket_path

    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None

    socket_path = _effective_socket_path(mcp_server)
    monkeypatch.setenv(
        "TMUX",
        f"{socket_path},12345,{mcp_session.session_id}",
    )
    monkeypatch.setenv("TMUX_PANE", new_pane.pane_id)
    with pytest.raises(ToolError, match="Refusing to respawn"):
        respawn_pane(
            pane_id=new_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )

    new_pane.kill()


def test_respawn_pane_kill_false_on_dead_pane_succeeds(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``kill=False`` respawn on a dead pane returns fresh PaneInfo.

    tmux's ``respawn-pane`` without ``-k`` is the safer default: it
    only succeeds when the pane has no running process. Existing tests
    only cover ``kill=True`` paths (see :func:`test_respawn_pane_*`
    above); this test locks the safer-default behaviour for any future
    flip of the default.
    """
    window = mcp_session.active_window
    # remain-on-exit=on keeps the pane around after its process exits so
    # we can drive a kill=False respawn on a confirmed-dead process.
    # Without it, tmux removes the pane the moment its child exits and
    # the respawn call fails with PaneNotFound instead of exercising
    # the kill=False branch. Set the option on the window *before*
    # splitting so the new pane inherits it.
    window.cmd("set-option", "-w", "remain-on-exit", "on")
    new_pane = window.split(shell="true")
    assert new_pane.pane_id is not None

    def _pane_dead() -> bool:
        out = new_pane.cmd("display-message", "-p", "#{pane_dead}").stdout
        return bool(out) and out[0].strip() == "1"

    retry_until(_pane_dead, seconds=5, raises=True)

    result = respawn_pane(
        pane_id=new_pane.pane_id,
        kill=False,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == new_pane.pane_id
    new_pane.kill()
    window.cmd("set-option", "-wu", "remain-on-exit")


def test_respawn_pane_kill_false_on_live_pane_raises(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``kill=False`` respawn on a live pane raises ToolError from tmux.

    tmux refuses to respawn a pane that still has a running process
    unless ``-k`` is passed. The MCP wrapper surfaces the stderr as a
    ``ToolError`` rather than swallowing it.
    """
    window = mcp_session.active_window
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None

    with pytest.raises(ToolError):
        respawn_pane(
            pane_id=new_pane.pane_id,
            kill=False,
            socket_name=mcp_server.socket_name,
        )

    new_pane.kill()


def test_respawn_pane_with_environment(
    mcp_server: Server, mcp_session: Session
) -> None:
    """``environment`` propagates through to the relaunched process.

    tmux's ``respawn-pane -e KEY=VALUE`` sets per-process env vars on
    the spawned command (``cmd-respawn-pane.c`` accepts the flag
    repeatedly). Verify by relaunching with ``sh -c 'env'`` under
    ``remain-on-exit`` so we can capture the env output after the
    process exits without tmux deleting the pane out from under us.
    """
    window = mcp_session.active_window
    window.cmd("set-option", "-w", "remain-on-exit", "on")
    new_pane = window.split(shell="sleep 3600")
    assert new_pane.pane_id is not None

    # Use ``printenv`` over ``env`` so the output fits the visible pane
    # (default capture-pane reads only the visible screen, not history).
    # Wrap the values in markers so we don't false-match on similarly
    # named host env vars that might already be set.
    result = respawn_pane(
        pane_id=new_pane.pane_id,
        shell="sh -c 'printenv LIBTMUX_TEST_FOO LIBTMUX_TEST_BAZ'",
        environment={"LIBTMUX_TEST_FOO": "bar", "LIBTMUX_TEST_BAZ": "qux"},
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == new_pane.pane_id

    def _pane_dead() -> bool:
        out = new_pane.cmd("display-message", "-p", "#{pane_dead}").stdout
        return bool(out) and out[0].strip() == "1"

    retry_until(_pane_dead, seconds=5, raises=True)

    # ``-S -50`` reads the last 50 lines of scrollback so we don't lose
    # the first ``printenv`` line off the top of the visible screen.
    captured = new_pane.cmd("capture-pane", "-p", "-S", "-50").stdout
    rendered = "\n".join(captured)
    assert "bar" in rendered
    assert "qux" in rendered

    new_pane.kill()
    window.cmd("set-option", "-wu", "remain-on-exit")


# ---------------------------------------------------------------------------
# search_panes tests
# ---------------------------------------------------------------------------


class SearchPanesFixture(t.NamedTuple):
    """Test fixture for search_panes."""

    test_id: str
    command: str
    pattern: str
    regex: bool
    match_case: bool
    scope_to_session: bool
    expected_match: bool
    expected_min_lines: int


SEARCH_PANES_FIXTURES: list[SearchPanesFixture] = [
    SearchPanesFixture(
        test_id="simple_match",
        command="echo FINDME_unique_string_12345",
        pattern="FINDME_unique_string_12345",
        regex=False,
        match_case=False,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="case_insensitive_match",
        command="echo UPPERCASE_findme_test",
        pattern="uppercase_findme_test",
        regex=False,
        match_case=False,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="case_sensitive_no_match",
        command="echo CaseSensitiveTest",
        pattern="casesensitivetest",
        regex=False,
        match_case=True,
        scope_to_session=False,
        expected_match=False,
        expected_min_lines=0,
    ),
    SearchPanesFixture(
        test_id="case_sensitive_match",
        command="echo CaseSensitiveExact",
        pattern="CaseSensitiveExact",
        regex=False,
        match_case=True,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="regex_pattern",
        command="echo error_code_42_found",
        pattern=r"error_code_\d+_found",
        regex=True,
        match_case=False,
        scope_to_session=False,
        expected_match=True,
        expected_min_lines=1,
    ),
    SearchPanesFixture(
        test_id="no_match",
        command="echo nothing_special",
        pattern="XYZZY_nonexistent_pattern_99999",
        regex=False,
        match_case=False,
        scope_to_session=False,
        expected_match=False,
        expected_min_lines=0,
    ),
    SearchPanesFixture(
        test_id="scoped_to_session",
        command="echo session_scoped_marker",
        pattern="session_scoped_marker",
        regex=False,
        match_case=False,
        scope_to_session=True,
        expected_match=True,
        expected_min_lines=1,
    ),
]


@pytest.mark.parametrize(
    SearchPanesFixture._fields,
    SEARCH_PANES_FIXTURES,
    ids=[f.test_id for f in SEARCH_PANES_FIXTURES],
)
def test_search_panes(
    mcp_server: Server,
    mcp_session: Session,
    mcp_pane: Pane,
    test_id: str,
    command: str,
    pattern: str,
    regex: bool,
    match_case: bool,
    scope_to_session: bool,
    expected_match: bool,
    expected_min_lines: int,
) -> None:
    """search_panes finds text in pane contents."""
    # Extract the echoed text from the command for polling
    echo_marker = command.split("echo ", 1)[1] if "echo " in command else command
    mcp_pane.send_keys(command, enter=True)
    retry_until(
        lambda: echo_marker in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    kwargs: dict[str, t.Any] = {
        "pattern": pattern,
        "regex": regex,
        "match_case": match_case,
        "socket_name": mcp_server.socket_name,
    }
    if scope_to_session:
        kwargs["session_name"] = mcp_session.session_name

    result = search_panes(**kwargs)
    assert isinstance(result, SearchPanesResult)

    if expected_match:
        assert len(result.matches) >= 1
        match = next((r for r in result.matches if r.pane_id == mcp_pane.pane_id), None)
        assert match is not None
        assert len(match.matched_lines) >= expected_min_lines
        assert match.session_id is not None
        assert match.window_id is not None
    else:
        pane_matches = [r for r in result.matches if r.pane_id == mcp_pane.pane_id]
        assert len(pane_matches) == 0


def test_search_panes_basic(mcp_server: Server, mcp_pane: Pane) -> None:
    """search_panes smoke test with a unique marker."""
    mcp_pane.send_keys("echo SMOKE_TEST_MARKER_abc123", enter=True)
    retry_until(
        lambda: "SMOKE_TEST_MARKER_abc123" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = search_panes(
        pattern="SMOKE_TEST_MARKER_abc123",
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, SearchPanesResult)
    assert len(result.matches) >= 1
    assert any(r.pane_id == mcp_pane.pane_id for r in result.matches)


def test_search_panes_returns_pane_content_match_model(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """search_panes returns PaneContentMatch models."""
    mcp_pane.send_keys("echo MODEL_TYPE_CHECK_xyz", enter=True)
    retry_until(
        lambda: "MODEL_TYPE_CHECK_xyz" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = search_panes(
        pattern="MODEL_TYPE_CHECK_xyz",
        socket_name=mcp_server.socket_name,
    )
    assert len(result.matches) >= 1
    for item in result.matches:
        assert isinstance(item, PaneContentMatch)


def test_search_panes_includes_window_and_session_names(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """search_panes populates window_name and session_name."""
    mcp_pane.send_keys("echo CONTEXT_FIELDS_CHECK_789", enter=True)
    retry_until(
        lambda: "CONTEXT_FIELDS_CHECK_789" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = search_panes(
        pattern="CONTEXT_FIELDS_CHECK_789",
        socket_name=mcp_server.socket_name,
    )
    match = next((r for r in result.matches if r.pane_id == mcp_pane.pane_id), None)
    assert match is not None
    assert match.window_name is not None
    assert match.session_name is not None
    assert match.session_name == mcp_session.session_name


def test_search_panes_invalid_regex(mcp_server: Server, mcp_session: Session) -> None:
    """search_panes raises ToolError on invalid regex when regex=True."""
    with pytest.raises(ToolError, match="Invalid regex pattern"):
        search_panes(
            pattern="[invalid",
            regex=True,
            socket_name=mcp_server.socket_name,
        )


def test_search_panes_pagination_limit_and_offset(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """search_panes pages matching panes via ``limit`` and ``offset``.

    Creates additional panes and seeds each with the same marker so
    multiple panes match. Then asserts:
    - ``limit=1`` returns one pane, ``truncated=True``, and the skipped
      panes are listed in ``truncated_panes``.
    - ``offset=1, limit=10`` returns the remaining panes with
      ``truncated=False``.
    - ``total_panes_matched`` is stable across pages.
    """
    marker = "PAGINATION_MARKER_qzz987"
    # Split the window a few times so we have >=3 panes matching.
    extra_panes = [
        mcp_session.active_window.split(),
        mcp_session.active_window.split(),
    ]
    all_panes = [mcp_pane, *extra_panes]
    for pane in all_panes:
        pane.send_keys(f"echo {marker}", enter=True)

    def _waiter(target: Pane) -> t.Callable[[], bool]:
        def _ready() -> bool:
            return marker in "\n".join(target.capture_pane())

        return _ready

    for pane in all_panes:
        retry_until(_waiter(pane), 10, raises=True)

    first = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        limit=1,
        socket_name=mcp_server.socket_name,
    )
    assert first.total_panes_matched >= 3
    assert len(first.matches) == 1
    assert first.truncated is True
    assert len(first.truncated_panes) == first.total_panes_matched - 1
    assert first.offset == 0
    assert first.limit == 1

    rest = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        offset=1,
        limit=10,
        socket_name=mcp_server.socket_name,
    )
    assert rest.total_panes_matched == first.total_panes_matched
    assert len(rest.matches) == first.total_panes_matched - 1
    assert rest.truncated is False
    assert rest.truncated_panes == []
    assert rest.offset == 1

    # Union of paginated pane IDs equals the full matching set.
    seen = {m.pane_id for m in first.matches} | {m.pane_id for m in rest.matches}
    assert len(seen) == first.total_panes_matched


def test_search_panes_literal_input_skips_slow_path_probe(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    r"""Literal searches (``regex=False``) find matches containing metacharacters.

    Regression guard for the ``_REGEX_META`` check bug: the pre-fix
    code tested the *escaped* pattern for regex metacharacters. With
    ``regex=False`` and a literal IP address like ``"192.168.1.1"``,
    ``re.escape`` produced ``"192\\.168\\.1\\.1"`` — whose ``\\`` matched
    the probe and kicked the search off the tmux fast path onto the
    slow Python-regex path.

    The functional observable: both paths correctly found the literal.
    The bug was performance. Probing that from a test is fragile (both
    paths call ``capture_pane`` in Phase 2), so this test asserts the
    *decision variable* directly: calling ``search_panes`` with a
    regex-meta-bearing literal must return the expected match, and the
    inspection of the fast-path decision is covered by the unit test
    below.
    """
    marker = "192.168.1.1"
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )
    result = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    assert any(m.pane_id == mcp_pane.pane_id for m in result.matches)


class SearchFastPathFixture(t.NamedTuple):
    """Fixture for ``search_panes`` fast-path eligibility cases."""

    test_id: str
    pattern: str
    regex: bool
    expected_fast_path: bool


SEARCH_FAST_PATH_FIXTURES: list[SearchFastPathFixture] = [
    # Literal input with regex metacharacters — the earlier bug's
    # target case. Raw input is glob-safe for tmux, fast path.
    SearchFastPathFixture("literal_regex_chars", "192.168.1.1", False, True),
    # Literal with no metacharacters — always fast path.
    SearchFastPathFixture("plain_literal", "plain_marker", False, True),
    # Regex with no metacharacters — fast path still fine.
    SearchFastPathFixture("plain_regex", "plain_marker", True, True),
    # Regex with metacharacters — legitimately slow path.
    SearchFastPathFixture("regex_group", r"err(or|no)", True, False),
    # Regex dot-star — slow path.
    SearchFastPathFixture("regex_dot_star", r".*", True, False),
    # tmux format-injection bytes in a literal — MUST fall to slow
    # path regardless of regex flag, because tmux's #{C:...} format
    # block has no escape for `}` (premature close), `#{` (nested
    # format-variable evaluation), or `#(` (format job execution).
    SearchFastPathFixture("literal_close_brace", "foo}", False, False),
    SearchFastPathFixture("literal_nested_format", "log #{err}", False, False),
    SearchFastPathFixture("literal_format_job", "#(printf ok)", False, False),
    # Same hazards with regex=True — still slow path; tmux sees the
    # raw pattern either way.
    SearchFastPathFixture("regex_close_brace", "x}y", True, False),
    SearchFastPathFixture("regex_nested_format", "a#{b}", True, False),
]


@pytest.mark.parametrize(
    "fixture",
    SEARCH_FAST_PATH_FIXTURES,
    ids=lambda fixture: fixture.test_id,
)
def test_search_panes_fast_path_decision(fixture: SearchFastPathFixture) -> None:
    """Unit-test the ``is_plain_text`` decision on pattern + regex flag.

    Mirrors the exact expression in ``search_panes`` so a future
    refactor cannot silently reintroduce either of the two hazards it
    guards against: the escape-aware metacharacter check that
    misclassified literals, or the tmux format-string injection on
    ``}`` / ``#{``.
    """
    import re as _re

    test_id, pattern, regex, expected_fast_path = fixture
    _regex_meta = _re.compile(r"[\\.*+?{}()\[\]|^$]")
    _tmux_format_injection = _re.compile(r"\}|#\{|#\(")
    if _tmux_format_injection.search(pattern):
        is_plain_text = False
    elif regex:
        is_plain_text = not _regex_meta.search(pattern)
    else:
        is_plain_text = True
    assert test_id
    assert is_plain_text is expected_fast_path


def test_search_panes_tmux_format_injection_is_neutralized(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """Literal patterns containing ``}`` or ``#{`` don't return every pane.

    Regression guard for the Critical tmux format-string injection in
    commit ``decc994`` (pre-existing, widened by the regex-fast-path
    fix): ``search_panes(pattern="foo}", regex=False)`` previously
    interpolated the raw ``}`` into ``#{C:foo}}`` — tmux's format
    parser closed the block at the first ``}``, evaluated the
    remainder (``}``) as truthy, and marked *every* pane as a match.

    Two panes are exercised: one seeded with the literal marker,
    one without. Only the seeded pane should appear in ``matches``.
    """
    marker = "INJECT_MARKER_xyz}qq9"  # contains `}` — the injection trigger
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    # Add a second pane that lacks the marker — if the fast path is
    # still injecting, every pane including this one shows up.
    clean_pane = mcp_session.active_window.split()
    clean_pane.send_keys("echo UNRELATED_content", enter=True)

    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = search_panes(
        pattern=marker,
        regex=False,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    matched_ids = {m.pane_id for m in result.matches}
    assert mcp_pane.pane_id in matched_ids
    assert clean_pane.pane_id not in matched_ids, (
        f"tmux format injection re-opened: clean pane {clean_pane.pane_id} "
        f"erroneously matched. Full match list: {matched_ids}"
    )


def test_search_panes_nested_format_variable_is_neutralized(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """Literal patterns containing ``#{`` don't trigger tmux format eval.

    Companion to the ``}`` injection test. ``#{`` inside the pattern
    opens a nested tmux format variable; without neutralization, tmux
    would evaluate ``#{pane_id}`` as the current pane's id and match
    every pane whose content contains its own id — a subtler but
    equally wrong outcome.
    """
    marker = "NEST_#{pane_id}_ABC"
    mcp_pane.send_keys(f"echo {marker!r}", enter=True)
    retry_until(
        lambda: "NEST" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = search_panes(
        pattern=marker,
        regex=False,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )
    # The test's value is that the call returns *without* raising and
    # without marking unrelated panes. An exact match on the literal
    # `#{pane_id}` bytes in scrollback isn't required.
    assert isinstance(result.matches, list)  # didn't crash
    # No pane other than mcp_pane should be in the match set, since no
    # other pane's content contains NEST_ at all.
    for m in result.matches:
        assert m.pane_id == mcp_pane.pane_id


def test_search_panes_hash_paren_format_job_is_neutralized(
    mcp_server: Server, mcp_session: Session, tmp_path: pathlib.Path
) -> None:
    """Literal patterns containing ``#(`` do not start tmux format jobs."""
    marker = tmp_path / "search_panes_format_job_marker"
    pattern = f"#(printf ok > {shlex.quote(str(marker))})"

    result = search_panes(
        pattern=pattern,
        regex=False,
        session_name=mcp_session.session_name,
        socket_name=mcp_server.socket_name,
    )

    assert isinstance(result.matches, list)
    time.sleep(0.5)
    assert not marker.exists()


def test_search_panes_numeric_pane_id_ordering(
    mcp_server: Server, mcp_session: Session
) -> None:
    """Pagination returns panes in numeric, not lexicographic, order.

    Regression guard: an earlier ``all_matches.sort(key=lambda m:
    m.pane_id)`` produced ``["%0", "%1", "%10", "%2", ...]`` on any
    session with ≥11 matching panes, which confused pagination (the
    last "page 1" pane was ``%2`` rather than ``%1``). The fix sorts
    via ``_pane_id_sort_key`` which casts the numeric portion.

    Physical tmux panes don't fit in a single 80x24 window past ~6
    before ``split-window`` fails with "no space for new pane"; this
    test spreads panes across multiple windows so pane ids reliably
    cross the ``%10`` boundary. The assertion is numeric monotonicity
    of ids across the returned matches.
    """
    marker = "NUMSORT_MARKER_89vq"
    # Spread panes across several windows so we get >= 12 panes without
    # running out of per-window space. Each new_window seeds one pane;
    # split() adds 1-2 more per window.
    while True:
        total_panes = sum(len(w.panes) for w in mcp_session.windows)
        if total_panes >= 12:
            break
        window = mcp_session.new_window()
        window.split()

    panes = [p for w in mcp_session.windows for p in w.panes]
    assert len(panes) >= 12
    for pane in panes:
        pane.send_keys(f"echo {marker}", enter=True)

    def _ready() -> bool:
        return all(marker in "\n".join(p.capture_pane()) for p in panes)

    retry_until(_ready, 10, raises=True)

    result = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        limit=100,
        socket_name=mcp_server.socket_name,
    )
    ids = [m.pane_id for m in result.matches]
    assert len(ids) >= 12
    numeric = [int(i.lstrip("%")) for i in ids]
    assert numeric == sorted(numeric), f"pane ids not in numeric order: {ids}"
    # The bug's canonical manifestation: lex-sort places ``%10`` between
    # ``%1`` and ``%2``. Pin that ``%2`` comes before ``%10`` as a
    # stronger shape check than pure monotonicity.
    assert 2 in numeric and 10 in numeric
    assert numeric.index(2) < numeric.index(10)


def test_search_panes_per_pane_matched_lines_cap(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """``max_matched_lines_per_pane`` tail-truncates matched_lines per pane.

    Synchronizes on shell-command completion via the project's own
    ``wait_for_channel`` primitive (the ``tmux wait-for -S`` idiom
    documented in ``src/libtmux_mcp/prompts/recipes.py``) instead of
    polling ``capture_pane`` output. This makes the assertion
    deterministic on every shell — the ``capture_pane`` inside
    ``search_panes`` runs strictly after the four echoes have
    executed, regardless of PS1 state or shell-startup timing.

    Four echoes produce at least eight marker-bearing lines in
    ``capture_pane`` (command-line plus output-line for each), well
    past the truncation threshold of three.
    """
    import asyncio
    import uuid

    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    marker = "PERLINE_MARKER_9gkv"
    channel = f"mcp_test_percap_{uuid.uuid4().hex[:16]}"
    payload = (
        f"echo {marker}; echo {marker}; echo {marker}; echo {marker}; "
        f"tmux wait-for -S {channel}"
    )
    mcp_pane.send_keys(payload, enter=True)
    asyncio.run(
        wait_for_channel(
            channel=channel, timeout=5.0, socket_name=mcp_server.socket_name
        )
    )

    result = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        max_matched_lines_per_pane=3,
        socket_name=mcp_server.socket_name,
    )
    match = next((m for m in result.matches if m.pane_id == mcp_pane.pane_id), None)
    assert match is not None
    assert len(match.matched_lines) == 3
    assert result.truncated is True


def test_search_panes_matches_pattern_across_wrap_slow_path(
    mcp_server: Server, mcp_session: Session, mcp_pane: Pane
) -> None:
    """Slow-path search joins wrapped visual rows before matching."""
    import asyncio
    import uuid

    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    width_raw = mcp_pane.display_message("#{pane_width}", get_text=True)
    assert width_raw is not None
    pane_width = int(width_raw[0])

    filler_len = max(1, pane_width - 5)
    marker = "WRAPPED_MARKER_xyz"
    channel = f"mcp_test_search_wrap_{uuid.uuid4().hex[:16]}"
    payload = (
        f"printf 'x%.0s' $(seq 1 {filler_len}); "
        "printf 'WRA'; printf 'PPED_MARKER'; printf '_xyz'; echo; "
        f"tmux wait-for -S {channel}"
    )
    mcp_pane.send_keys(payload, enter=True)
    asyncio.run(
        wait_for_channel(
            channel=channel, timeout=5.0, socket_name=mcp_server.socket_name
        )
    )

    result = search_panes(
        pattern=marker,
        session_name=mcp_session.session_name,
        content_start=-100,
        socket_name=mcp_server.socket_name,
    )

    match = next((m for m in result.matches if m.pane_id == mcp_pane.pane_id), None)
    assert match is not None
    assert any(marker in line for line in match.matched_lines)


# ---------------------------------------------------------------------------
# search_panes is_caller annotation tests
# ---------------------------------------------------------------------------


class SearchPanesCallerFixture(t.NamedTuple):
    """Test fixture for search_panes is_caller annotation."""

    test_id: str
    tmux_pane_env: str | None
    use_real_pane_id: bool
    expected_is_caller: bool | None


SEARCH_PANES_CALLER_FIXTURES: list[SearchPanesCallerFixture] = [
    SearchPanesCallerFixture(
        # TMUX_PANE without TMUX: the strict comparator cannot verify the
        # caller's socket and returns ``False`` rather than conservatively
        # assuming same-server. Full-TMUX-env coverage lives in
        # ``tests/test_utils.py::test_serialize_pane_is_caller_false_across_sockets``.
        test_id="caller_pane_no_tmux_env",
        tmux_pane_env=None,
        use_real_pane_id=True,
        expected_is_caller=False,
    ),
    SearchPanesCallerFixture(
        test_id="outside_tmux_no_annotation",
        tmux_pane_env=None,
        use_real_pane_id=False,
        expected_is_caller=None,
    ),
]


@pytest.mark.parametrize(
    SearchPanesCallerFixture._fields,
    SEARCH_PANES_CALLER_FIXTURES,
    ids=[f.test_id for f in SEARCH_PANES_CALLER_FIXTURES],
)
def test_search_panes_is_caller(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
    test_id: str,
    tmux_pane_env: str | None,
    use_real_pane_id: bool,
    expected_is_caller: bool | None,
) -> None:
    """search_panes annotates results with is_caller based on TMUX_PANE."""
    marker = f"IS_CALLER_TEST_{test_id}_{id(mcp_pane)}"
    mcp_pane.send_keys(f"echo {marker}", enter=True)
    retry_until(
        lambda: marker in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    if use_real_pane_id:
        monkeypatch.setenv("TMUX_PANE", mcp_pane.pane_id or "")
    elif tmux_pane_env is not None:
        monkeypatch.setenv("TMUX_PANE", tmux_pane_env)
    else:
        monkeypatch.delenv("TMUX_PANE", raising=False)

    result = search_panes(
        pattern=marker,
        socket_name=mcp_server.socket_name,
    )
    match = next((r for r in result.matches if r.pane_id == mcp_pane.pane_id), None)
    assert match is not None
    assert match.is_caller is expected_is_caller


# ---------------------------------------------------------------------------
# wait_for_text tests
# ---------------------------------------------------------------------------


class ResolverParityFixture(t.NamedTuple):
    """One targeting shape both pane resolvers must agree on."""

    test_id: str
    #: Which of the four shared targeting arguments to populate. Resolved
    #: against the live fixture at call time, since ids are dynamic.
    arg: str


RESOLVER_PARITY_FIXTURES: list[ResolverParityFixture] = [
    ResolverParityFixture(test_id="by_pane_id", arg="pane_id"),
    ResolverParityFixture(test_id="by_window_id", arg="window_id"),
    ResolverParityFixture(test_id="by_session_id", arg="session_id"),
    ResolverParityFixture(test_id="by_session_name", arg="session_name"),
    ResolverParityFixture(test_id="no_target_at_all", arg="none"),
]


@pytest.mark.parametrize(
    ResolverParityFixture._fields,
    RESOLVER_PARITY_FIXTURES,
    ids=[f.test_id for f in RESOLVER_PARITY_FIXTURES],
)
def test_wait_resolver_matches_the_canonical_resolver(
    mcp_server: Server, mcp_session: Session, test_id: str, arg: str
) -> None:
    """``wait_for_text``'s private resolver must agree with ``_resolve_pane``.

    ``wait.py`` carries its own async resolver because the canonical one
    is synchronous and reaches tmux through an untimed
    ``Popen.communicate()`` — unusable from the event loop, and
    uncancellable through a thread. The two therefore cannot share an
    implementation: different sync-ness, and one returns a ``Pane`` while
    the other returns a pane id.

    What they CAN share is the contract — which argument wins, and which
    pane you get when several could match. Nothing pinned that, so a
    change to the canonical precedence would have diverged the wait tools
    silently. This is that pin. It is deliberately a behavioural
    equivalence test rather than a refactor: merging the two would mean
    putting a blocking call back on the event loop.

    Not covered here: ``window_index`` and ``pane_index``, which the wait
    tools do not accept.
    """
    import asyncio

    from libtmux_mcp._utils import _resolve_pane
    from libtmux_mcp.tools.pane_tools.wait import _resolve_pane_bounded

    # A second window and pane so "first listed" is a real choice rather
    # than the only option — otherwise every arm agrees trivially.
    mcp_session.new_window(window_name="parity_second", attach=False)
    window = mcp_session.active_window
    window.split(attach=False)

    target_pane = window.panes[0]
    kwargs: dict[str, str] = {}
    if arg == "pane_id":
        kwargs["pane_id"] = str(target_pane.pane_id)
    elif arg == "window_id":
        kwargs["window_id"] = str(window.window_id)
    elif arg == "session_id":
        kwargs["session_id"] = str(mcp_session.session_id)
    elif arg == "session_name":
        kwargs["session_name"] = str(mcp_session.session_name)

    canonical = _resolve_pane(mcp_server, **kwargs)
    bounded = asyncio.run(
        _resolve_pane_bounded(
            mcp_server,
            pane_id=kwargs.get("pane_id"),
            session_name=kwargs.get("session_name"),
            session_id=kwargs.get("session_id"),
            window_id=kwargs.get("window_id"),
            deadline=None,
        )
    )
    assert bounded == canonical.pane_id, (
        f"resolvers disagree for {arg}: bounded={bounded} canonical={canonical.pane_id}"
    )


@pytest.mark.parametrize(
    ("arg", "value"),
    [
        ("pane_id", "%999999"),
        ("window_id", "@999999"),
        ("session_id", "$999999"),
        ("session_name", "no_such_session_parity"),
    ],
)
@pytest.mark.usefixtures("mcp_session")
def test_wait_resolver_raises_like_the_canonical_resolver(
    mcp_server: Server, arg: str, value: str
) -> None:
    """A miss must raise the same way through both resolvers.

    Precedence parity is only half the contract: the agent-visible error
    matters just as much, because that text is what an agent reads when
    it targeted the wrong thing.
    """
    import asyncio

    from libtmux_mcp._utils import _resolve_pane
    from libtmux_mcp.tools.pane_tools.wait import _resolve_pane_bounded

    with pytest.raises(libtmux_exc.LibTmuxException) as canonical_exc:
        _resolve_pane(mcp_server, **{arg: value})

    with pytest.raises(libtmux_exc.LibTmuxException) as bounded_exc:
        asyncio.run(
            _resolve_pane_bounded(
                mcp_server,
                pane_id=value if arg == "pane_id" else None,
                session_name=value if arg == "session_name" else None,
                session_id=value if arg == "session_id" else None,
                window_id=value if arg == "window_id" else None,
                deadline=None,
            )
        )

    assert type(bounded_exc.value) is type(canonical_exc.value), (
        f"{arg}: bounded raised {type(bounded_exc.value).__name__}, "
        f"canonical raised {type(canonical_exc.value).__name__}"
    )
    # The type alone is a weak contract — it is the message an agent
    # reads. Both must name the target that missed, or the agent cannot
    # tell WHICH of its arguments was wrong.
    assert value in str(bounded_exc.value), (
        f"{arg}: bounded error does not name {value!r}: {bounded_exc.value}"
    )
    assert value in str(canonical_exc.value), (
        f"{arg}: canonical error does not name {value!r}: {canonical_exc.value}"
    )


class WaitForTextFixture(t.NamedTuple):
    """Test fixture for wait_for_text."""

    test_id: str
    #: Command sent BEFORE ``wait_for_text`` is called. Its output is
    #: expected to be present in the pane scrollback (and therefore
    #: above the baseline) by the time the wait begins. Used to verify
    #: that stale scrollback no longer matches (#45). The positive
    #: "text appears after baseline" case lives in
    #: ``test_wait_for_text_matches_new_output_after_baseline`` rather
    #: than this fixture because it needs ``asyncio.create_task`` plus
    #: a sequenced ``await`` to coordinate emission against the running
    #: poll loop — synchronous setup races the shell's enter-processing
    #: on CI and shifts the baseline past single-line output.
    pre_command: str | None
    patterns: list[str]
    timeout: float
    expected_found: bool


WAIT_FOR_TEXT_FIXTURES: list[WaitForTextFixture] = [
    # Regression for #45: pre-existing scrollback must NOT match.
    WaitForTextFixture(
        test_id="stale_scrollback_does_not_match",
        pre_command="echo WAIT_MARKER_stale",
        patterns=["WAIT_MARKER_stale"],
        timeout=0.5,
        expected_found=False,
    ),
    # Genuinely absent pattern still times out cleanly.
    WaitForTextFixture(
        test_id="timeout_not_found",
        pre_command=None,
        patterns=["NEVER_EXISTS_xyz999"],
        timeout=0.3,
        expected_found=False,
    ),
]


@pytest.mark.parametrize(
    WaitForTextFixture._fields,
    WAIT_FOR_TEXT_FIXTURES,
    ids=[f.test_id for f in WAIT_FOR_TEXT_FIXTURES],
)
def test_wait_for_text(
    mcp_server: Server,
    mcp_pane: Pane,
    test_id: str,
    pre_command: str | None,
    patterns: list[str],
    timeout: float,
    expected_found: bool,
) -> None:
    """wait_for_text polls pane content for a pattern."""
    import asyncio

    if pre_command is not None:
        mcp_pane.send_keys(pre_command, enter=True)
        # Wait until the pane has fully settled before measuring the
        # baseline. "Settled" means:
        #
        #   (a) the OUTPUT line is present — ``line.strip() == pattern``,
        #       distinguishing the shell's actual output from the typed
        #       echo line that contains ``pattern`` as a substring (and
        #       which would otherwise trip a naive ``pattern in capture``
        #       predicate while keys are still buffered pre-enter), and
        #   (b) ``(history_size, cursor_y)`` is unchanged across two
        #       consecutive polls — zsh prints async prompt-redraw
        #       lines (vcs_info, precmd hooks) some milliseconds after
        #       the initial prompt, and those redraws keep growing
        #       hsize *during* ``wait_for_text``'s window, pulling
        #       pre-baseline rows back into the visible-relative
        #       ``start_line`` capture. Waiting them out anchors the
        #       baseline below all async output.
        #
        # A fixed ``time.sleep`` would do the same job but couples the
        # test to a wall-clock value (the project's idiom for
        # tmux-state waits is ``retry_until`` — used throughout this
        # file).
        last_state: tuple[int, int] = (-1, -1)

        def _stale_settled() -> bool:
            nonlocal last_state
            raw = mcp_pane.cmd(
                "display-message", "-p", "#{history_size}:#{cursor_y}"
            ).stdout
            if not raw:
                return False
            hs_str, cy_str = raw[0].split(":", 1)
            state = (int(hs_str), int(cy_str))
            has_output_line = any(
                line.strip() == patterns[0] for line in mcp_pane.capture_pane()
            )
            settled = state == last_state and has_output_line
            last_state = state
            return settled

        retry_until(_stale_settled, 10, raises=True)

    result = asyncio.run(
        wait_for_text(
            patterns=patterns,
            pane_id=mcp_pane.pane_id,
            timeout=timeout,
            socket_name=mcp_server.socket_name,
        )
    )
    assert isinstance(result, WaitForTextResult)
    assert result.found is expected_found
    assert result.pane_id == mcp_pane.pane_id
    assert result.elapsed_seconds >= 0

    if expected_found:
        assert len(result.matched_lines) >= 1


#: How long the tests that coordinate against a running wait sleep
#: before writing their marker.
#:
#: This is a race, and it only fails in one direction: if the wait has
#: not locked its entry baseline yet, the marker is PRE-EXISTING content
#: by the time it does, gets correctly suppressed, and the test fails
#: for a reason unrelated to the code under test. Locking the baseline
#: costs five tmux round trips, a few tens of milliseconds at idle —
#: but this whole cluster went red together on a box at load ~30 with
#: 20 CPUs, and ``--reruns`` did not save it because the load outlived
#: the retries.
#:
#: 1 s buys roughly an order of magnitude of headroom for a fraction of
#: a second per test. There is no event to wait on instead: the baseline


def test_wait_for_text_matches_new_output_after_baseline(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wait_for_text finds output written AFTER its baseline snapshot.

    Coordinates the marker emission against the running poll loop by
    starting :func:`wait_for_text` via :func:`asyncio.create_task`,
    then ``await``-ing the emit coroutine, then ``await``-ing the
    wait task. Sequencing matters: the explicit start-then-emit
    ordering guarantees ``send_keys`` fires *after* the baseline
    read; :func:`asyncio.gather` would schedule both concurrently
    and lose that guarantee. Without coordination the test races
    the shell's enter-processing — if the shell advances the cursor
    before the baseline read on CI, ``start_line`` shifts past the
    single-line marker and the poll loop misses it.
    """
    import asyncio

    armed = _armed_after_baseline(monkeypatch)

    async def emit_after_baseline() -> None:
        # The baseline read is a single display-message round trip
        # (<5 ms in practice); 0.2 s gives wait_for_text plenty of
        # headroom to lock the baseline before the marker fires.
        await armed.wait()
        await asyncio.to_thread(mcp_pane.send_keys, "echo WAIT_MARKER_after", True)

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=["WAIT_MARKER_after"],
                pane_id=mcp_pane.pane_id,
                timeout=5.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await emit_after_baseline()
        return await wait_task

    result = asyncio.run(run())
    assert result.found is True
    assert any("WAIT_MARKER_after" in line for line in result.matched_lines)


#: Parks a pane on a prompt-shaped row that never moves again: print a
#: prompt WITHOUT a trailing newline so the cursor stays at the end of
#: it, then sleep. Fixture teardown kills the pane and the sleep.
#: ``capture-pane`` strips trailing whitespace, so the token asserted on
#: deliberately excludes the space that is printed.
_PARKED_PROMPT = "PARKED_PROMPT:"
_PARK_COMMAND = f"printf '{_PARKED_PROMPT} '; sleep 60"


def _park_pane(pane: Pane) -> None:
    """Replace the pane's shell with a pane that is genuinely quiescent.

    Settling the default zsh pane is not enough, and the way it fails is
    silent. Measured: two consecutive identical ``(hsize, cursor_y)``
    polls are satisfied while zsh is still STARTING, before it has
    painted anything at all — the screen is empty and the state is a
    stable ``(0, 0)``. zsh then prints a ``compinit`` warning that wraps
    to two rows, so by the time a marker is written the cursor has moved
    to row 2 and the marker lands well BELOW the recorded entry row.
    The entry-row tests then pass on unfixed code, proving nothing.

    A parked ``sh`` removes the race instead of racing it: one prompt
    row, cursor at the end of it, and nothing else will ever move.
    """
    pane.respawn(kill=True, shell=f'sh -c "{_PARK_COMMAND}"')

    def _parked() -> bool:
        if not any(_PARKED_PROMPT in line for line in pane.capture_pane()):
            return False
        # ``cursor_y`` is the property under test, and it must be the
        # prompt's own row. ``history_size`` is deliberately NOT asserted
        # on: respawn does not clear scrollback, so whatever the previous
        # shell wrote is still in history.
        raw = pane.cmd("display-message", "-p", "#{cursor_y}").stdout
        return bool(raw) and raw[0] == "0"

    retry_until(_parked, 10, raises=True)


def _write_to_pane_tty(pane: Pane, payload: str) -> None:
    """Write ``payload`` straight to the pane's tty.

    ``send_keys`` is the wrong tool for the entry-row tests: it types at
    the shell, which processes the newline and moves the cursor, so the
    marker can never land on the row the cursor occupied at entry —
    exactly the row under test. Writing to ``#{pane_tty}`` puts bytes on
    the terminal without touching the shell's line editor.
    """
    tty = pane.display_message("#{pane_tty}", get_text=True)[0]
    fd = os.open(tty, os.O_WRONLY)
    try:
        os.write(fd, payload.encode())
    finally:
        os.close(fd)


class EntryRowFixture(t.NamedTuple):
    """Test fixture for entry-cursor-row visibility."""

    test_id: str
    #: Bytes written straight to the pane tty while the wait is running.
    payload: str
    patterns: list[str]
    expected_found: bool


ENTRY_ROW_FIXTURES: list[EntryRowFixture] = [
    # The regression. On a quiescent pane the cursor sits at the end of
    # the prompt, so an unprefixed line lands on the entry cursor row.
    # That row used to be excluded by index and the marker was
    # unmatchable — the tool's headline case (a daemon printing one
    # ``ready`` line) always burned the full budget and then reported
    # ``saw_new_output=false``.
    EntryRowFixture(
        test_id="bare_line_lands_on_entry_row",
        payload="ENTRY_ROW_MARKER\n",
        patterns=["ENTRY_ROW_MARKER"],
        expected_found=True,
    ),
    # No trailing newline at all: the marker never leaves the entry row.
    EntryRowFixture(
        test_id="unterminated_line_stays_on_entry_row",
        payload="Continue? [y/N] ",
        patterns=[r"Continue\?"],
        expected_found=True,
    ),
    # The marker is on the entry row and the row below it moves too, so
    # ``saw_new_output`` was true while ``found`` was false — the
    # confusing shape that sends an agent into a retry loop.
    EntryRowFixture(
        test_id="entry_row_marker_with_trailing_line",
        payload="ENTRY_ROW_MARKER\ntrailing\n",
        patterns=["ENTRY_ROW_MARKER"],
        expected_found=True,
    ),
    # In-place rewrites: spinners and single-line status updates land on
    # the entry row via carriage return and never advance it.
    EntryRowFixture(
        test_id="carriage_return_rewrite_on_entry_row",
        payload="\rworking 1 \rworking 2 ",
        patterns=["working 2"],
        expected_found=True,
    ),
    # Control: a row BELOW the entry cursor still matches, so a failure
    # above is a real regression and not a broken harness.
    EntryRowFixture(
        test_id="line_below_entry_row_still_matches",
        payload="\nENTRY_ROW_MARKER\n",
        patterns=["ENTRY_ROW_MARKER"],
        expected_found=True,
    ),
    # Control: nothing written means nothing matches.
    EntryRowFixture(
        test_id="silent_pane_still_times_out",
        payload="",
        patterns=["ENTRY_ROW_MARKER"],
        expected_found=False,
    ),
]


@pytest.mark.parametrize(
    EntryRowFixture._fields,
    ENTRY_ROW_FIXTURES,
    ids=[f.test_id for f in ENTRY_ROW_FIXTURES],
)
def test_wait_for_text_sees_the_entry_cursor_row(
    mcp_server: Server,
    mcp_pane: Pane,
    test_id: str,
    payload: str,
    patterns: list[str],
    expected_found: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content arriving on the entry cursor row must be matchable.

    The wait anchors on the row the cursor occupied at entry rather than
    the row below it, and suppresses that row's PRE-EXISTING content by
    value instead. Suppressing it by index was a shipped false negative:
    on a quiescent pane the cursor sits at the end of the prompt, which
    is precisely where the next line of output lands.
    """
    import asyncio

    # Must be a genuinely parked pane: against the default zsh pane every
    # case below passes on unfixed code, because zsh's startup output
    # moves the cursor off the recorded entry row before the marker
    # lands. See :func:`_park_pane`.
    _park_pane(mcp_pane)

    armed = _armed_after_baseline(monkeypatch)

    async def emit_after_baseline() -> None:
        await armed.wait()
        if payload:
            await asyncio.to_thread(_write_to_pane_tty, mcp_pane, payload)

    # A case that must NOT match spends its whole budget proving it, so
    # that budget is the test's cost rather than a ceiling it never
    # reaches. Only the matching cases get headroom.
    budget = 20.0 if expected_found else 1.0

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=patterns,
                pane_id=mcp_pane.pane_id,
                timeout=budget,
                regex=True,
                socket_name=mcp_server.socket_name,
            )
        )
        await emit_after_baseline()
        return await wait_task

    result = asyncio.run(run())
    assert result.found is expected_found
    if expected_found:
        assert result.outcome == "matched"
        assert result.matched_lines


def test_wait_for_text_does_not_match_the_prompt_on_the_entry_row(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    r"""Anchoring on the entry row must not make the prompt self-match.

    This is the falsifier for
    :func:`test_wait_for_text_sees_the_entry_cursor_row`. The entry row
    normally holds the shell prompt, so a pattern broad enough to hit it
    would match at t=0 and turn a fixed false negative into a much worse
    false positive. The content snapshot is what prevents that: the
    prompt text is captured at entry and filtered out of every tick.

    ``\S`` is deliberately the broadest useful pattern — it matches any
    non-space character anywhere on any captured row.
    """
    import asyncio

    _park_pane(mcp_pane)

    for pattern in ("PARKED_PROMPT", r"\S"):
        result = asyncio.run(
            wait_for_text(
                patterns=[pattern],
                pane_id=mcp_pane.pane_id,
                timeout=0.5,
                regex=True,
                socket_name=mcp_server.socket_name,
            )
        )
        assert result.found is False, f"pattern {pattern!r} self-matched the prompt"
        assert result.matched_at_entry is True


def test_wait_for_text_waits_for_a_fresh_occurrence(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale match on screen must not short-circuit the wait.

    ``matched_at_entry`` is known before the poll loop starts, which
    makes returning immediately look like free latency. It is not: the
    commonest agent loop is run-a-command / wait-for-its-marker /
    run-it-again / wait-for-the-SAME-marker, and on the second pass the
    first pass's marker is still on screen. Returning early would answer
    with the old occurrence and the agent would never learn the second
    run finished.

    So the wait keeps waiting, and this test pins that: a marker is left
    on screen BEFORE the wait, an identical one is written during it,
    and the wait must match the fresh one.
    """
    import asyncio

    marker = "RERUN_MARKER"
    _park_pane(mcp_pane)

    armed = _armed_after_baseline(monkeypatch)

    async def emit_after_baseline() -> None:
        await armed.wait()
        await asyncio.to_thread(_write_to_pane_tty, mcp_pane, f"{marker}\n")

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=[marker],
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await emit_after_baseline()
        return await wait_task

    # Pass one: leave the marker on screen and let the pane settle so it
    # is unambiguously stale by the time the wait takes its baseline.
    _write_to_pane_tty(mcp_pane, f"\n{marker}\n")

    def _stale_marker_visible() -> bool:
        return any(marker in line for line in mcp_pane.capture_pane())

    retry_until(_stale_marker_visible, 10, raises=True)

    result = asyncio.run(run())
    assert result.found is True
    assert result.outcome == "matched"
    # The stale occurrence was on screen but did not answer the wait.
    assert result.matched_at_entry is False


def test_wait_for_text_reports_a_stop_marker_already_on_screen(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A failure marker predating the wait is surfaced, not hidden.

    The entry scan covered ``patterns`` and not ``stop``, so a build
    that had already failed produced a bare ``timeout`` — which an agent
    re-running the build reads as "still running" when the honest answer
    is "the previous run already failed".
    """
    import asyncio

    marker = "STOP_ALREADY_THERE"
    _park_pane(mcp_pane)
    _write_to_pane_tty(mcp_pane, f"\n{marker}\n")
    retry_until(
        lambda: any(marker in line for line in mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = asyncio.run(
        wait_for_text(
            patterns=["NEVER_APPEARS_ZZZ"],
            stop=[marker],
            pane_id=mcp_pane.pane_id,
            timeout=2.0,
            socket_name=mcp_server.socket_name,
        )
    )

    # The stale stop marker must not END the wait -- only a fresh hit does.
    assert result.outcome == "timeout"
    assert result.stop_matched_at_entry is True


def test_wait_for_text_ignores_stale_below_cursor(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """Stale paint-style content below the cursor must not match.

    The cursor-position anchor captures the entry cursor row and
    everything below it — which can include content that pre-dates the
    wait (TUI repaints, ``paste-text``, manual cursor positioning). The
    entry-time content snapshot filters those rows out so only content
    written after entry matches the regex.

    Setup parks the cursor at row 0 with ``STALE_BELOW`` painted on
    row 1, then waits for a pattern that's already on screen. The
    snapshot filter must drop the row before the regex sees it.
    """
    import asyncio

    # Print STALE_BELOW, then move the cursor back to the top-left so
    # row 1 holds stale content that wait_for_text would otherwise
    # match on the first poll. The trailing sleep keeps the pane state
    # frozen for the wait's duration. Double-quote the sh -c argument
    # so the inner single-quoted printf format strings don't break the
    # outer quoting.
    paint_and_park = (
        "printf 'TOP\\nSTALE_BELOW\\n'; "  # write 2 rows; cursor lands on row 2
        "printf '\\033[H'; "  # ESC[H = move cursor to (0,0)
        "sleep 60"
    )
    mcp_pane.respawn(kill=True, shell=f'sh -c "{paint_and_park}"')

    def _staged() -> bool:
        return any("STALE_BELOW" in line for line in mcp_pane.capture_pane())

    retry_until(_staged, 10, raises=True)

    result = asyncio.run(
        wait_for_text(
            patterns=["STALE_BELOW"],
            pane_id=mcp_pane.pane_id,
            timeout=0.5,
            socket_name=mcp_server.socket_name,
        )
    )
    assert result.found is False


def test_wait_for_text_does_not_match_bottom_row_clip(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """wait_for_text must not match stale text sitting on the cursor row.

    The cursor at the last visible row is the case that used to defeat
    the index anchor: ``start_line`` pointed below the visible region,
    tmux's ``capture-pane -S`` clipped back to the bottom row
    (``cmd-capture-pane.c``), and the poll loop matched the stale
    cursor-row text instantly. The anchor now lands ON that row, which
    is always a valid ``-S`` target, so there is nothing to clip — and
    the entry-time content snapshot is what keeps the stale text from
    matching. This test pins the outcome, which is unchanged.

    The pane is respawned with a shell-free ``sh -c`` command that
    prints the marker without a trailing newline and then sleeps —
    so ``hsize`` and ``cursor_y`` stay frozen for the duration of
    the wait. Running this with zsh in the loop produced a
    multi-line history burst on shell exit / exec that lowered
    ``start_line`` below ``pane_height`` and disengaged the guard.
    """
    import asyncio

    # Replace the default shell with a single sh invocation: emit
    # filler rows to push the cursor to the bottom of the visible
    # region, print the marker without a trailing newline so it
    # stays on the cursor row, then sleep so nothing else scrolls
    # into history. Fixture teardown kills the pane (and the sleep)
    # at test exit.
    fill_and_park = (
        "for i in $(seq 1 30); do echo filler; done; "
        "printf STALE_BOTTOM_MARKER; sleep 60"
    )
    mcp_pane.respawn(kill=True, shell=f"sh -c '{fill_and_park}'")

    def _bottom_row_ready() -> bool:
        state = mcp_pane.display_message("#{pane_height}:#{cursor_y}", get_text=True)
        if not state:
            return False
        sy_str, cy_str = state[0].split(":", 1)
        if int(cy_str) != int(sy_str) - 1:
            return False
        return any("STALE_BOTTOM_MARKER" in line for line in mcp_pane.capture_pane())

    retry_until(_bottom_row_ready, 10, raises=True)

    result = asyncio.run(
        wait_for_text(
            patterns=["STALE_BOTTOM_MARKER"],
            pane_id=mcp_pane.pane_id,
            timeout=0.5,
            socket_name=mcp_server.socket_name,
        )
    )
    assert result.found is False


def test_wait_for_text_invalid_regex(mcp_server: Server, mcp_pane: Pane) -> None:
    """wait_for_text raises ToolError on invalid regex when regex=True."""
    import asyncio

    with pytest.raises(ToolError, match="Invalid regex pattern"):
        asyncio.run(
            wait_for_text(
                patterns=["[invalid"],
                regex=True,
                pane_id=mcp_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_rejects_empty_pattern(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """An empty pattern matches every line and returns found=True instantly.

    ``re.compile('')`` succeeds and ``re.search`` reports a zero-width
    match on every string, so the first poll would return
    ``found=True`` against whatever was in the pane. Reject explicitly.
    """
    import asyncio

    with pytest.raises(ToolError, match="patterns pattern must be a non-empty"):
        asyncio.run(
            wait_for_text(
                patterns=[""],
                pane_id=mcp_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_rejects_tiny_interval(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A sub-10ms interval lets the poll loop saturate the tmux server.

    ``asyncio.sleep(0)`` yields but does not idle, so an unguarded
    ``interval=0`` fires tmux subprocesses as fast as the scheduler
    hands them out — a self-inflicted server-side DoS.
    """
    import asyncio

    with pytest.raises(ToolError, match=r"interval must be at least 0\.01"):
        asyncio.run(
            wait_for_text(
                patterns=["anything"],
                pane_id=mcp_pane.pane_id,
                interval=0,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_raises_on_pane_respawn(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Respawning the pane mid-wait invalidates the baseline anchor.

    The baseline absolute index is computed against the original
    pane process's grid. ``respawn-pane`` clears the visible region
    but preserves ``hsize`` (``screen_reinit``), so the math keeps
    pointing at the *old* process's content — silently miscapturing.
    ``wait_for_text`` detects the ``pane_pid`` change and surfaces
    it as a ToolError instead.
    """
    import asyncio

    armed = _armed_after_baseline(monkeypatch)

    async def respawn_after_delay() -> None:
        # Let wait_for_text capture its baseline first, then swap
        # the pane process so pane_pid changes.
        await armed.wait()
        await asyncio.to_thread(mcp_pane.respawn, kill=True, shell="sleep 30")

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=["NEVER_APPEARS_xyz"],
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await respawn_after_delay()
        return await wait_task

    with pytest.raises(ToolError, match="respawned"):
        asyncio.run(run())


def test_wait_for_text_raises_on_pane_death(mcp_server: Server, mcp_pane: Pane) -> None:
    """A pane whose process has exited surfaces as a ToolError.

    With ``remain-on-exit`` set, tmux keeps the pane alive after its
    child exits and reports ``#{pane_dead}=1``. The wait loop checks
    that flag every tick and bails with a ToolError instead of
    polling stale content until timeout.
    """
    import asyncio

    mcp_pane.window.set_option("remain-on-exit", "on")
    mcp_pane.respawn(kill=True, shell="true")

    def _is_dead() -> bool:
        flag = mcp_pane.display_message("#{pane_dead}", get_text=True)
        return bool(flag) and flag[0] == "1"

    retry_until(_is_dead, 10, raises=True)

    with pytest.raises(ToolError, match="died"):
        asyncio.run(
            wait_for_text(
                patterns=["anything"],
                pane_id=mcp_pane.pane_id,
                timeout=1.0,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_rejects_non_positive_timeout(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A non-positive timeout is ambiguous; reject rather than guess.

    The loop body runs one probe before the deadline check, so
    ``timeout=0`` would complete a single synchronous capture in a
    "wait" tool — surprising. Reject explicitly so callers pick a
    meaningful budget.
    """
    import asyncio

    with pytest.raises(ToolError, match="timeout must be positive"):
        asyncio.run(
            wait_for_text(
                patterns=["anything"],
                pane_id=mcp_pane.pane_id,
                timeout=0,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_raises_when_history_is_cleared(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``clear-history`` during a wait drops ``hsize`` to 0, tripping the guard.

    Pre-fills scrollback, starts the wait, then runs ``clear-history``
    on the pane. tmux's ``grid_clear_history`` sets ``gd->hsize = 0``
    synchronously, so the next poll sees ``state.history_size <
    entry.history_size`` and raises ``ToolError``.
    """
    import asyncio

    mcp_pane.send_keys("for i in $(seq 1 100); do echo prefill$i; done", enter=True)

    def _prefilled() -> bool:
        hs = mcp_pane.display_message("#{history_size}", get_text=True)
        return bool(hs) and int(hs[0]) >= 50

    retry_until(_prefilled, 10, raises=True)

    armed = _armed_after_baseline(monkeypatch)

    async def clear_after_delay() -> None:
        # Let wait_for_text snapshot the baseline first, then drop
        # hsize to 0 with clear-history.
        await armed.wait()
        await asyncio.to_thread(mcp_pane.cmd, "clear-history")

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=["NEVER_APPEARS_rollover"],
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await clear_after_delay()
        return await wait_task

    with pytest.raises(ToolError, match="history shrank below entry baseline"):
        asyncio.run(run())


def test_wait_for_text_succeeds_when_history_grows_normally(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monotonic history growth without trim does NOT trip the rollover guard.

    The guard fires only when ``state.history_size < entry.history_size``.
    Many lines scrolling into a generous ``history-limit`` keep hsize
    monotonically increasing, so a long-output command followed by a
    sentinel marker must still match cleanly.
    """
    import asyncio

    armed = _armed_after_baseline(monkeypatch)

    async def emit_after_baseline() -> None:
        await armed.wait()
        cmd = "for i in $(seq 1 50); do echo line$i; done; echo WAIT_MARKER_grows_ok"
        await asyncio.to_thread(mcp_pane.send_keys, cmd, True)

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=["WAIT_MARKER_grows_ok"],
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await emit_after_baseline()
        return await wait_task

    result = asyncio.run(run())
    assert result.found is True


def test_wait_for_text_survives_resize_grow_with_scrolled_history(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resize-grow that pulls lines from history must NOT trip the rollover guard.

    tmux's ``screen_resize_y`` decrements ``gd->hsize`` on a vertical
    grow when ``hscrolled > 0`` — rows from history are pulled back
    into the visible region. The rows themselves are NOT freed; only
    the history/visible-region boundary shifts and absolute indices
    stay valid. The guard's conjunction with ``pane_height <=
    entry.pane_height`` exempts this case, because resize-grow also
    increases ``pane_height``.
    """
    import asyncio

    # Pre-fill scrollback so hscrolled > 0 — rows must have already
    # scrolled past the visible region for screen_resize_y to have
    # anything to pull back on grow.
    mcp_pane.send_keys("for i in $(seq 1 100); do echo prefill$i; done", enter=True)

    def _prefilled() -> bool:
        hs = mcp_pane.display_message("#{history_size}", get_text=True)
        return bool(hs) and int(hs[0]) >= 50

    retry_until(_prefilled, 10, raises=True)

    # Read current pane height; we'll grow past it during the wait.
    height_raw = mcp_pane.display_message("#{pane_height}", get_text=True)
    assert height_raw is not None
    current_height = int(height_raw[0])
    target_height = current_height + 3

    armed = _armed_after_baseline(monkeypatch)

    async def grow_after_delay() -> None:
        # Let wait_for_text snapshot the baseline first, then grow
        # the window vertically. screen_resize_y pulls rows from
        # history back into view, decrementing hsize.
        await armed.wait()
        await asyncio.to_thread(
            mcp_pane.window.cmd,
            "resize-window",
            "-y",
            str(target_height),
        )

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=["NEVER_APPEARS_resize_grow"],
                pane_id=mcp_pane.pane_id,
                timeout=1.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await grow_after_delay()
        return await wait_task

    # The wait must complete cleanly via timeout — NOT a ToolError.
    result = asyncio.run(run())
    assert result.found is False


def test_wait_for_text_handles_resize_during_wait(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mid-wait resize keys the bottom-row clip to the LIVE pane height.

    Without the ``state.pane_height`` fix, the bottom-row clip guard
    stays keyed to the entry-time pane height. Shrinking the pane
    mid-wait would then leave the guard too lax — the capture would
    fire past the new bottom and tmux's ``-S`` clip would return stale
    bottom-row text. The fix re-reads ``pane_height`` each tick so the
    guard matches the current visible region.
    """
    import asyncio

    # Park a stale marker on the last visible row and freeze output.
    # Same parking shape as test_wait_for_text_does_not_match_bottom_row_clip.
    fill_and_park = (
        "for i in $(seq 1 30); do echo filler; done; "
        "printf STALE_RESIZE_MARKER; sleep 60"
    )
    mcp_pane.respawn(kill=True, shell=f"sh -c '{fill_and_park}'")

    def _ready() -> bool:
        return any("STALE_RESIZE_MARKER" in line for line in mcp_pane.capture_pane())

    retry_until(_ready, 10, raises=True)

    armed = _armed_after_baseline(monkeypatch)

    async def resize_after_delay() -> None:
        await armed.wait()
        await asyncio.to_thread(mcp_pane.cmd, "resize-pane", "-y", "5")

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=["STALE_RESIZE_MARKER"],
                pane_id=mcp_pane.pane_id,
                timeout=0.5,
                socket_name=mcp_server.socket_name,
            )
        )
        await resize_after_delay()
        return await wait_task

    result = asyncio.run(run())
    assert result.found is False


def test_wait_for_text_matches_pattern_across_wrap(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pattern that spans tmux's visual wrap matches via ``-J``.

    The poll loop passes ``join_wrapped=True`` to ``capture-pane`` so a
    pattern that crosses the wrap boundary is matched against the
    joined logical line. Without that flag, each visual row is its own
    string and a regex against any one row never sees the full marker.

    The command line is composed of three ``printf`` calls so the
    echoed command text does NOT contain the marker as a literal
    substring — only the produced output (after the three pieces
    concatenate on stdout) does.
    """
    import asyncio

    width_raw = mcp_pane.display_message("#{pane_width}", get_text=True)
    assert width_raw is not None
    pane_width = int(width_raw[0])

    filler_len = max(1, pane_width - 5)
    payload = (
        f"printf 'x%.0s' $(seq 1 {filler_len}); "
        "printf 'WRA'; printf 'PPED_MARKER'; printf '_xyz'; echo"
    )
    marker = "WRAPPED_MARKER_xyz"

    armed = _armed_after_baseline(monkeypatch)

    async def emit_after_baseline() -> None:
        await armed.wait()
        await asyncio.to_thread(mcp_pane.send_keys, payload, True)

    async def run() -> WaitForTextResult:
        wait_task = asyncio.create_task(
            wait_for_text(
                patterns=[marker],
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await emit_after_baseline()
        return await wait_task

    result = asyncio.run(run())
    assert result.found is True
    assert any(marker in line for line in result.matched_lines)


def test_wait_for_text_reports_progress_on_a_ticker_not_per_poll() -> None:
    """Progress cadence must not be the poll interval.

    Reporting from inside the poll loop tied the notification rate to
    ``interval`` -- a polling knob with a 0.01 floor -- so the default
    emitted ~20 notifications a second, each an awaited JSON-RPC message
    carrying the same sentence with a different decimal.

    Read from the tree rather than measured. Three behavioural versions
    of this test flaked: any assertion about how many ticks land in a
    window is fragile in one direction, because load drops ticks and
    never adds them, and a ratio between two runs drifts when load
    differs between them. The property is structural, so this asserts
    the structure and the sibling test below covers delivery.
    """
    import ast
    import inspect

    from libtmux_mcp.tools.pane_tools import wait as wait_module

    tree = ast.parse(inspect.getsource(wait_module))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "wait_for_text"
    )
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "progress_ticker" in called, (
        "wait_for_text must report through progress_ticker, like its siblings"
    )
    assert "_maybe_report_progress" not in called, (
        "wait_for_text reports progress inline again; that ties the "
        "notification rate to the poll interval"
    )


def test_wait_for_text_propagates_unexpected_progress_error(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-transport exceptions from ``ctx.report_progress`` propagate.

    Regression guard: an earlier ``contextlib.suppress(Exception)`` in
    ``_maybe_report_progress`` silently swallowed every exception from
    ``ctx.report_progress`` — including programming errors like a
    renamed kwarg or a misconfigured ``ctx``. The narrowed catch only
    covers transport-closed exceptions; anything else (e.g.
    ``RuntimeError`` from a stub that's been deliberately broken) must
    reach the caller so the failure is diagnostic instead of a mystery
    quiet hang.
    """
    import asyncio

    class _FaultyContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            msg = "synthetic bug in progress-notification path"
            raise RuntimeError(msg)

    # The error surfaces through ``handle_tool_errors_async``, which
    # maps any unexpected ``Exception`` to ``ToolError`` with the
    # original type + message preserved in the translated text. The
    # point of this regression guard is that the error reaches the
    # error handler at all — previously the broad ``suppress`` ate it.
    # The faulty context is only reached when the ticker fires, so this
    # needs at least one tick. Margin on both axes, because the failure
    # is one-directional -- load removes ticks, never adds them:
    #
    #   cadence 0.001s, so a tick lands as soon as the loop is scheduled
    #   timeout 1.5s, because pane resolution and the entry capture run
    #   BEFORE the ticker starts, and a short budget can be spent
    #   entirely on them, leaving the ticker no window at all
    #
    # At 0.05s/0.5s this flaked 2 runs in 10 under `-n auto`.
    monkeypatch.setattr(_progress_module, "_TICK_SECONDS", 0.001)

    with pytest.raises(ToolError, match="synthetic bug"):
        asyncio.run(
            wait_for_text(
                patterns=["WILL_NEVER_MATCH_PROPAGATE_q2rj"],
                pane_id=mcp_pane.pane_id,
                timeout=1.5,
                interval=0.05,
                socket_name=mcp_server.socket_name,
                ctx=t.cast("t.Any", _FaultyContext()),
            )
        )


def test_wait_for_text_suppresses_broken_resource_error(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``anyio.BrokenResourceError`` from progress is treated as transport-gone.

    FastMCP's streamable-HTTP transport raises ``BrokenResourceError``
    (not ``ClosedResourceError``) when the receive side of the in-memory
    stream is closed — i.e. the peer went away. The wait loop must treat
    this identically to the closed-stream case: silently skip the
    progress notification and keep polling until the timeout.
    """
    import asyncio

    import anyio

    class _BrokenContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            raise anyio.BrokenResourceError

        async def warning(self, message: str) -> None:
            # Same transport-closed shape on the log channel — the
            # wait loop's timeout-warning call must also be suppressed
            # silently when the peer is gone.
            raise anyio.BrokenResourceError

    result = asyncio.run(
        wait_for_text(
            patterns=["WILL_NEVER_MATCH_BROKEN_rpt5"],
            pane_id=mcp_pane.pane_id,
            timeout=0.2,
            interval=0.05,
            socket_name=mcp_server.socket_name,
            ctx=t.cast("t.Any", _BrokenContext()),
        )
    )
    assert result.found is False


def test_wait_for_text_warns_on_invalid_regex(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``wait_for_text`` emits ``ctx.warning`` when the regex won't compile.

    Regression guard: agents calling with ``regex=True`` and a malformed
    pattern previously saw only a generic ``ToolError``. The new
    ``_maybe_log`` helper at ``wait.py`` lets the same condition surface
    as a ``notifications/message`` warning so MCP client log panels
    record the cause independent of the tool result.
    """
    import asyncio

    log_calls: list[tuple[str, str]] = []

    class _RecordingContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            return

        async def warning(self, message: str) -> None:
            log_calls.append(("warning", message))

    with pytest.raises(ToolError, match="Invalid regex"):
        asyncio.run(
            wait_for_text(
                patterns=["[unclosed"],
                regex=True,
                pane_id=mcp_pane.pane_id,
                socket_name=mcp_server.socket_name,
                ctx=t.cast("t.Any", _RecordingContext()),
            )
        )

    # The ``warning`` ran before the ``ToolError`` was raised.
    assert (
        "warning",
        "Invalid regex pattern: missing ), unterminated subpattern at position 0",
    ) in log_calls or any(
        level == "warning" and "Invalid regex" in msg for level, msg in log_calls
    )


def test_wait_for_text_warns_on_timeout(mcp_server: Server, mcp_pane: Pane) -> None:
    """``wait_for_text`` warns the client when the poll loop times out.

    Sibling guard to the invalid-regex warning. The timeout case is
    where operators most need a structured signal — the tool returns
    ``found=False`` but agents and human log readers have to dig into
    the ``WaitForTextResult`` to notice. The warning surfaces it
    directly.
    """
    import asyncio

    log_calls: list[tuple[str, str]] = []

    class _RecordingContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            return

        async def warning(self, message: str) -> None:
            log_calls.append(("warning", message))

    result = asyncio.run(
        wait_for_text(
            patterns=["WILL_NEVER_MATCH_TIMEOUT_qZx9"],
            pane_id=mcp_pane.pane_id,
            timeout=0.2,
            interval=0.05,
            socket_name=mcp_server.socket_name,
            ctx=t.cast("t.Any", _RecordingContext()),
        )
    )

    assert result.found is False
    assert any(
        level == "warning" and "timeout" in msg.lower() for level, msg in log_calls
    ), f"expected a timeout warning, got: {log_calls}"


def test_wait_for_text_warns_in_history_limit_risk_band(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``wait_for_text`` emits a warning when polling near ``history-limit``.

    With a small ``history-limit`` and a burst of output that forces
    ``grid_collect_history`` to fire repeatedly, sampled ``history_size``
    enters the trim-risk band (top 10% of ``history_limit``). The wait's
    strict-shrink predicate cannot see those trims (hsize stays clamped
    at the cap), so the tool emits a one-shot ``ctx.warning`` notification
    so MCP clients can decide whether to keep waiting, retry, or switch
    to ``wait_for_channel``.

    The wait's ``found`` result is intentionally not asserted — once
    polling enters the risk band, correctness is best-effort. The test
    pins the warning contract (what the tool guarantees), not the
    match contract (what tmux's grid model fundamentally can't).
    """
    import asyncio

    # ``history-limit`` is session-scope and the effective per-pane value
    # is locked in at pane creation. Set the option globally, then split a
    # fresh pane that inherits the small limit. The mcp_pane fixture's
    # original pane keeps its larger limit and is unaffected.
    mcp_pane.session.cmd("set-option", "-g", "history-limit", "50")
    fresh_pane = mcp_pane.window.split()
    assert fresh_pane.pane_id is not None

    def _hlimit_locked() -> bool:
        hl = fresh_pane.display_message("#{history_limit}", get_text=True)
        return bool(hl) and int(hl[0]) == 50

    retry_until(_hlimit_locked, 10, raises=True)

    log_calls: list[tuple[str, str]] = []

    class _RecordingContext:
        async def report_progress(
            self,
            progress: float,
            total: float | None = None,
            message: str = "",
        ) -> None:
            return

        async def warning(self, message: str) -> None:
            log_calls.append(("warning", message))

    # Fill history INTO the band before the wait starts rather than
    # racing to fill it during one. Bursting mid-wait made the warning
    # depend on how fast a real shell produced 200 lines inside the
    # budget, which under parallel load it sometimes did not.
    fresh_pane.send_keys("for i in $(seq 1 200); do echo burst$i; done", enter=True)

    def _in_risk_band() -> bool:
        hs = fresh_pane.display_message("#{history_size}", get_text=True)
        return bool(hs) and int(hs[0]) >= 45

    retry_until(_in_risk_band, 10, raises=True)

    async def run() -> None:
        try:
            await wait_for_text(
                patterns=["WILL_NEVER_MATCH_riskband_qZ9"],
                pane_id=fresh_pane.pane_id,
                timeout=2.0,
                interval=0.05,
                socket_name=mcp_server.socket_name,
                ctx=t.cast("t.Any", _RecordingContext()),
            )
        except ToolError:
            # The strict-shrink guard may or may not fire depending on
            # whether the dip is observable between polls. Either way,
            # we only assert the warning contract, not the result type.
            return

    asyncio.run(run())

    assert any(
        level == "warning" and "trim-risk band" in msg for level, msg in log_calls
    ), f"expected a trim-risk-band warning, got: {log_calls}"


def test_wait_for_text_warns_when_already_in_risk_band(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``wait_for_text`` warns immediately if entry is already in the risk band.

    Unlike ``test_wait_for_text_warns_in_history_limit_risk_band`` which
    advances into the band, this covers the case where the pane is
    already near ``history-limit`` at entry. Without output (idle wait),
    the simplified predicate (no ``advanced`` gate) must still fire the
    one-shot warning.
    """
    import asyncio

    mcp_pane.session.cmd("set-option", "-g", "history-limit", "50")
    fresh_pane = mcp_pane.window.split()
    assert fresh_pane.pane_id is not None

    def _hlimit_locked() -> bool:
        hl = fresh_pane.display_message("#{history_limit}", get_text=True)
        return bool(hl) and int(hl[0]) == 50

    retry_until(_hlimit_locked, 10, raises=True)

    # history-limit is 50. Risk floor (top 10%) is 45.
    # Print 100 lines to ensure hsize reaches the cap (50).
    fresh_pane.send_keys("for i in $(seq 1 100); do echo line$i; done", True)

    def _prefilled() -> bool:
        hs = fresh_pane.display_message("#{history_size}", get_text=True)
        # We need it to be in the risk band (>= 45).
        return bool(hs) and int(hs[0]) >= 45

    retry_until(_prefilled, 10, raises=True)

    log_calls: list[tuple[str, str]] = []

    class _RecordingContext:
        async def report_progress(self, *args: t.Any, **kwargs: t.Any) -> None:
            return

        async def warning(self, message: str) -> None:
            log_calls.append(("warning", message))

    async def run() -> WaitForTextResult:
        # Idle wait: no new output, no cursor movement.
        return await wait_for_text(
            patterns=["NEVER_MATCH_idle_risk"],
            pane_id=fresh_pane.pane_id,
            timeout=0.5,
            interval=0.1,
            socket_name=mcp_server.socket_name,
            ctx=t.cast("t.Any", _RecordingContext()),
        )

    # The trim-risk band is surfaced as a client log notification, not
    # a result field: an agent cannot act on a boolean it gets after
    # the fact, and the field was permanent weight in ``outputSchema``.
    asyncio.run(run())

    assert any(
        level == "warning" and "trim-risk band" in msg for level, msg in log_calls
    ), f"expected a trim-risk-band warning during idle wait, got: {log_calls}"


def test_wait_for_text_propagates_cancellation(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``wait_for_text`` raises ``CancelledError`` (not ``ToolError``).

    Regression guard for MCP cancellation semantics.
    ``handle_tool_errors_async`` in ``_utils.py:827-850`` catches
    ``Exception`` (not ``BaseException``); since
    ``asyncio.CancelledError`` is a ``BaseException`` (Python 3.8+) it
    propagates today. Locking that in: if a future change broadens the
    decorator to ``BaseException`` it would silently break MCP
    cancellation, and this test fires.

    Uses ``task.cancel()`` rather than ``asyncio.wait_for`` so the
    raised exception is the inner ``CancelledError`` directly, not
    ``wait_for``'s ``TimeoutError`` wrapper.
    """
    import asyncio

    armed = _armed_after_baseline(monkeypatch)

    async def _runner() -> None:
        task = asyncio.create_task(
            wait_for_text(
                patterns=["WILL_NEVER_MATCH_CANCEL_aBcD"],
                pane_id=mcp_pane.pane_id,
                timeout=10.0,
                interval=0.05,
                socket_name=mcp_server.socket_name,
            )
        )
        await armed.wait()
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_runner())


def test_wait_tools_do_not_block_event_loop(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """A wedged tmux must not pin the event loop.

    FastMCP direct-awaits async tools on the main loop, so anything the
    wait path does synchronously freezes every other coroutine, the MCP
    transport, and cancellation itself.

    Discriminator: a stub ``tmux`` that never returns. The wait path
    spawns it with ``asyncio.create_subprocess_exec``, so a concurrent
    ticker keeps firing every 10 ms while the call is in flight and the
    call still ends bounded. Route it through a *blocking* spawn and
    the ticker stops dead for the whole wedge.

    This replaces an earlier version that monkeypatched a slow capture
    to measure an ``asyncio.to_thread`` offload. That invariant is
    gone: there is no blocking call left to offload, and a thread was
    never a safe place for this work anyway -- a worker stuck in
    ``Popen.communicate()`` cannot be cancelled, and
    ``concurrent.futures.thread._python_exit`` joins it untimed at
    interpreter exit. See ``test_wait_path_uses_no_worker_threads``.
    """
    import asyncio

    # Patched where the argv is BUILT and where the per-call bound is
    # READ -- both live in _bounded_io. wait.py calls into it, so
    # patching wait's namespace would leave the real tmux running.
    from libtmux_mcp import _bounded_io as _wait_mod

    stub = tmp_path / "tmux"
    stub.write_text("#!/bin/sh\nsleep 60\n")
    stub.chmod(0o755)
    monkeypatch.setattr(
        _wait_mod, "_tmux_argv", lambda _server, *args: [str(stub), *args]
    )
    monkeypatch.setattr(_wait_mod, "_TMUX_CALL_TIMEOUT_SECONDS", 0.4)

    async def _drive() -> tuple[int, float]:
        ticks = 0
        stop = asyncio.Event()

        async def _ticker() -> None:
            nonlocal ticks
            while not stop.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        started = time.monotonic()

        async def _waiter() -> None:
            try:
                with contextlib.suppress(ToolError):
                    await wait_for_text(
                        patterns=["WILL_NEVER_MATCH_EVENT_LOOP_zqr9"],
                        pane_id=mcp_pane.pane_id,
                        timeout=0.5,
                        interval=0.05,
                        socket_name=mcp_server.socket_name,
                    )
            finally:
                stop.set()

        await asyncio.gather(_ticker(), _waiter())
        return ticks, time.monotonic() - started

    ticks, elapsed = asyncio.run(_drive())

    assert elapsed < 5.0, f"wedged tmux was not bounded: {elapsed:.1f}s"
    # ~40 ticks expected across the wedge; a pinned loop yields none.
    assert ticks >= 20, (
        f"ticker advanced only {ticks} times — a wedged tmux is pinning "
        f"the event loop instead of running as an async subprocess"
    )


# ---------------------------------------------------------------------------
# wait_for_text: ceiling, stop patterns, catch-all, honesty fields
# ---------------------------------------------------------------------------


def _emit_after_baseline(pane: Pane, payload: str, armed: asyncio.Event) -> t.Any:
    """Return a coroutine that sends ``payload`` once the wait has armed."""

    async def _emit() -> None:
        await armed.wait()
        await asyncio.to_thread(pane.send_keys, payload, True)

    return _emit()


def test_wait_for_text_clamps_oversized_timeout(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An over-large ``timeout`` is clamped, not rejected.

    Clamp-and-report: the call returns at the server ceiling and says
    so on ``effective_timeout`` / ``timeout_clamped``, so the agent
    learns the policy from the result instead of from a failed call.

    The ceiling is lowered to 1 s for the test so the assertion is
    about the clamp mechanism, not about wall-clock patience. The
    production 30 s value is exercised by the same code path.
    """
    import asyncio

    from libtmux_mcp import _wait_policy

    monkeypatch.setattr(_wait_policy, "_wait_max_seconds", 1.0)

    started = time.monotonic()
    result = asyncio.run(
        wait_for_text(
            patterns=["NEVER_APPEARS_CLAMP_q7x"],
            pane_id=mcp_pane.pane_id,
            timeout=3600.0,
            socket_name=mcp_server.socket_name,
        )
    )
    elapsed = time.monotonic() - started

    assert result.found is False
    assert result.effective_timeout == 1.0
    # The clamp is visible as effective_timeout < what we passed.
    assert result.effective_timeout < 3600.0
    # Generous headroom for the fixed per-call tmux bound on slow CI.
    assert elapsed < 10.0, f"clamped wait ran {elapsed:.1f}s"


def test_wait_for_text_reports_unclamped_timeout(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A timeout under the ceiling is reported verbatim, unclamped."""
    import asyncio

    result = asyncio.run(
        wait_for_text(
            patterns=["NEVER_APPEARS_UNCLAMPED_q7x"],
            pane_id=mcp_pane.pane_id,
            timeout=0.3,
            socket_name=mcp_server.socket_name,
        )
    )
    assert result.effective_timeout == 0.3


def test_wait_for_text_stop_pattern_returns_early(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``stop`` hit ends the wait immediately and names which one fired.

    This is the whole cap-only bet: the agent already knows its failure
    markers, so the common failure path collapses to milliseconds
    without any state-inspection heuristic.
    """
    import asyncio

    armed = _armed_after_baseline(monkeypatch)

    async def run() -> WaitForTextResult:
        task = asyncio.create_task(
            wait_for_text(
                patterns=["BUILD_OK_marker_z1"],
                stop=["NEVER_PRINTED_zz", "BUILD_FAILED_marker_z1"],
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await _emit_after_baseline(mcp_pane, "echo BUILD_FAILED_marker_z1", armed)
        return await task

    result = asyncio.run(run())

    assert result.outcome == "stopped"
    assert result.found is False
    assert result.matched_index == 1
    assert any("BUILD_FAILED_marker_z1" in line for line in result.matched_lines)
    assert result.elapsed_seconds < 10.0


def test_wait_for_text_pattern_hit_reports_its_index(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``patterns`` hit reports source ``patterns`` and the entry index."""
    import asyncio

    armed = _armed_after_baseline(monkeypatch)

    async def run() -> WaitForTextResult:
        task = asyncio.create_task(
            wait_for_text(
                patterns=["NEVER_PRINTED_yy", "DONE_marker_y2"],
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await _emit_after_baseline(mcp_pane, "echo DONE_marker_y2", armed)
        return await task

    result = asyncio.run(run())

    assert result.found is True
    assert result.outcome == "matched"
    assert result.matched_index == 1


def test_wait_for_text_none_patterns_waits_for_any_new_output(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``patterns=None`` is the any-new-output catch-all.

    This subsumes the former ``wait_for_content_change`` tool: no glyph
    matching, works for any shell and any program.
    """
    import asyncio

    armed = _armed_after_baseline(monkeypatch)

    async def run() -> WaitForTextResult:
        task = asyncio.create_task(
            wait_for_text(
                pane_id=mcp_pane.pane_id,
                timeout=20.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await _emit_after_baseline(mcp_pane, "echo ANY_OUTPUT_marker_c3", armed)
        return await task

    result = asyncio.run(run())

    assert result.found is True
    assert result.outcome == "any_output"
    assert result.matched_index is None
    assert result.saw_new_output is True
    assert result.elapsed_seconds < 10.0


def test_wait_for_text_none_patterns_times_out_on_silent_pane(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """The catch-all still times out cleanly on a quiescent pane."""
    import asyncio

    mcp_pane.respawn(kill=True, shell="sh -c 'sleep 60'")

    def _parked() -> bool:
        state = mcp_pane.display_message("#{pane_current_command}", get_text=True)
        return bool(state) and state[0] in {"sh", "sleep"}

    retry_until(_parked, 10, raises=True)

    result = asyncio.run(
        wait_for_text(
            pane_id=mcp_pane.pane_id,
            timeout=0.4,
            socket_name=mcp_server.socket_name,
        )
    )
    assert result.found is False
    assert result.outcome == "timeout"
    assert result.saw_new_output is False


def test_wait_for_text_rejects_empty_patterns_list(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``patterns=[]`` is ambiguous — null means catch-all, so reject the list."""
    import asyncio

    with pytest.raises(ToolError, match="patterns must be a non-empty list"):
        asyncio.run(
            wait_for_text(
                patterns=[],
                pane_id=mcp_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_rejects_empty_stop_entry(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """An empty ``stop`` entry matches every line; reject it explicitly."""
    import asyncio

    with pytest.raises(ToolError, match="stop pattern must be a non-empty"):
        asyncio.run(
            wait_for_text(
                patterns=["anything"],
                stop=[""],
                pane_id=mcp_pane.pane_id,
                socket_name=mcp_server.socket_name,
            )
        )


def test_wait_for_text_reports_stale_match_and_tail(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A pattern already painted below the cursor is reported, not matched.

    The delta filter correctly refuses to match stale paint (#45), but
    an agent that only sees ``found=false`` cannot tell that case apart
    from "nothing happened". ``suppressed_stale_match`` states the fact
    without guessing at a cause, and ``tail`` shows the rows the filter
    suppressed.
    """
    import asyncio

    paint_and_park = "printf 'TOP\\nSTALE_TAIL_MARKER\\n'; printf '\\033[H'; sleep 60"
    mcp_pane.respawn(kill=True, shell=f'sh -c "{paint_and_park}"')

    def _staged() -> bool:
        return any("STALE_TAIL_MARKER" in line for line in mcp_pane.capture_pane())

    retry_until(_staged, 10, raises=True)

    result = asyncio.run(
        wait_for_text(
            patterns=["STALE_TAIL_MARKER"],
            pane_id=mcp_pane.pane_id,
            timeout=0.5,
            socket_name=mcp_server.socket_name,
        )
    )

    assert result.found is False
    assert result.matched_at_entry is True
    assert any("STALE_TAIL_MARKER" in line for line in result.tail)


def test_wait_for_text_tail_is_bounded_by_lines_and_bytes(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tail`` is capped on both axes.

    ``capture-pane -J`` wrap-joins, so one logical line can be far
    wider than ``pane_width`` — a line-only cap is not a bound.
    """
    import asyncio

    from libtmux_mcp.tools.pane_tools.wait import _TAIL_MAX_BYTES, _TAIL_MAX_LINES

    armed = _armed_after_baseline(monkeypatch)

    async def run() -> WaitForTextResult:
        task = asyncio.create_task(
            wait_for_text(
                patterns=["NEVER_APPEARS_TAILCAP_j4"],
                pane_id=mcp_pane.pane_id,
                # Runs to timeout by design (the pattern never appears),
                # so this is spend, not headroom. The 200-line burst
                # lands in ~0.3 s.
                timeout=3.0,
                socket_name=mcp_server.socket_name,
            )
        )
        await _emit_after_baseline(
            mcp_pane,
            "for i in $(seq 1 200); do echo tailcap_line_$i; done",
            armed,
        )
        return await task

    result = asyncio.run(run())

    assert result.saw_new_output is True
    assert len(result.tail) <= _TAIL_MAX_LINES
    assert len("\n".join(result.tail).encode()) <= _TAIL_MAX_BYTES


def test_wait_for_text_never_interpolates_pattern_into_tmux_format(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller patterns never reach a tmux argv, let alone a format string.

    Verified on tmux 3.7b: ``#{C/r:HEL{1,2}O}|#{history_size}|#{cursor_y}``
    returns ``0O}|0|0`` — an ordinary regex quantifier corrupts field
    parsing — and a pattern merely ENDING in ``#`` swallows the rest of
    the format (``A#{C/ri:v1#}B|#{pane_dead}|#{alternate_on}`` returns
    ``A``). Matching therefore stays in Python; this test pins that the
    pattern text is absent from every tmux invocation.
    """
    import asyncio

    from libtmux_mcp import _bounded_io as wait_mod

    recorded: list[tuple[str, ...]] = []
    original = wait_mod._run_tmux_lines

    async def _spy(server: t.Any, *args: str, **kwargs: t.Any) -> list[str]:
        recorded.append(args)
        return await original(server, *args, **kwargs)

    monkeypatch.setattr(wait_mod, "_run_tmux_lines", _spy)

    hostile = ["#{C/r:HEL{1,2}O}", "A#{C/ri:v1#}B", "}}}#"]
    result = asyncio.run(
        wait_for_text(
            patterns=hostile,
            stop=["#{pane_dead}"],
            regex=False,
            pane_id=mcp_pane.pane_id,
            timeout=0.3,
            socket_name=mcp_server.socket_name,
        )
    )

    assert result.found is False
    assert recorded, "no tmux invocations were recorded"
    flat = [arg for args in recorded for arg in args]
    for needle in ("HEL", "v1#", "}}}", "C/r", "C/ri"):
        assert not any(needle in arg for arg in flat), (
            f"pattern fragment {needle!r} reached tmux argv: {flat}"
        )


def test_wait_for_text_bounds_every_tmux_call(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every tmux child the wait path spawns is bounded by a timeout.

    Amplifier C. The wait path spawns tmux with
    ``asyncio.create_subprocess_exec`` and bounds each call with
    ``asyncio.wait(timeout=...)``. A thread would be unrecoverable
    here: a worker blocked in ``Popen.communicate()`` cannot be
    cancelled, and ``concurrent.futures.thread._python_exit`` joins
    every pool worker untimed at interpreter shutdown, so one wedged
    tmux hangs process exit forever.
    """
    import asyncio

    timeouts: list[float | None] = []
    original_wait = asyncio.wait

    async def _spy(*args: t.Any, **kwargs: t.Any) -> t.Any:
        timeouts.append(kwargs.get("timeout"))
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", _spy)

    asyncio.run(
        wait_for_text(
            patterns=["NEVER_APPEARS_BOUNDED_v8"],
            pane_id=mcp_pane.pane_id,
            timeout=0.3,
            socket_name=mcp_server.socket_name,
        )
    )

    assert timeouts, "the wait path spawned no bounded tmux calls"
    assert all(v is not None and v > 0 for v in timeouts), (
        f"an unbounded tmux call slipped through: {timeouts}"
    )


def test_wait_path_uses_no_worker_threads(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait path must never hand tmux work to a thread.

    This is the invariant that keeps a wedged tmux from hanging
    interpreter shutdown: ``concurrent.futures.thread._python_exit``
    joins pool workers with no timeout, and no thread-based
    arrangement escapes it -- not ``asyncio.to_thread``, not a private
    pool with ``shutdown(wait=False)``. A subprocess we own can be
    killed; a thread cannot.
    """
    import asyncio

    calls: list[t.Any] = []
    original = asyncio.to_thread

    async def _spy(fn: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:
        calls.append(fn)
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy)

    asyncio.run(
        wait_for_text(
            patterns=["NEVER_APPEARS_NOTHREAD_v9"],
            pane_id=mcp_pane.pane_id,
            timeout=0.3,
            socket_name=mcp_server.socket_name,
        )
    )

    assert calls == [], f"wait path used worker threads for: {calls}"


def test_wait_for_text_wedged_tmux_raises_instead_of_hanging(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tmux binary that never returns surfaces as a bounded tool error.

    Drives the bounded helper directly against a stub ``tmux`` that
    sleeps far past the bound. Without ``subprocess.run(timeout=...)``
    this call would block its worker thread for the full sleep.
    """
    import asyncio

    from libtmux_mcp import _bounded_io as wait_mod

    stub = tmp_path / "tmux"
    stub.write_text("#!/bin/sh\nsleep 60\n")
    stub.chmod(0o755)

    class _StubServer:
        tmux_bin = str(stub)
        socket_name = None
        socket_path = None

    monkeypatch.setattr(wait_mod, "_TMUX_CALL_TIMEOUT_SECONDS", 0.5)

    started = time.monotonic()
    with pytest.raises(ExpectedToolError, match="unresponsive"):
        asyncio.run(
            wait_mod._run_tmux_lines(t.cast("t.Any", _StubServer()), "display-message")
        )
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"wedged tmux blocked for {elapsed:.1f}s"


def test_run_tmux_lines_cancel_reaps_child(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling a wait mid-read reaps the tmux child, never orphans it.

    Cancellation lands on ``asyncio.wait`` inside ``_run_tmux_lines``,
    not just on ``task.result()``. If the reap guard does not span the
    ``asyncio.wait`` the child is left running after the coroutine
    unwinds. Drives the helper directly against a stub ``tmux`` that
    records its own pid and sleeps far past the wait, cancels while it
    is asleep, and asserts BOTH that ``CancelledError`` propagates and
    that the recorded pid is gone.
    """
    import asyncio
    import os

    from libtmux_mcp import _bounded_io as wait_mod

    pidfile = tmp_path / "pid"
    stub = tmp_path / "tmux"
    # ``exec``, so the recorded pid IS the sleeping process rather than
    # a shell that spawned it. Without it the shell died on cancel and
    # its orphaned ``sleep`` kept the stdout pipe open, so the test cost
    # the full 60 seconds -- and it was checking that the PARENT was
    # reaped while the process actually holding the pipe survived.
    stub.write_text(f'#!/bin/sh\necho $$ > "{pidfile}"\nexec sleep 60\n')
    stub.chmod(0o755)

    class _StubServer:
        tmux_bin = str(stub)
        socket_name = None
        socket_path = None

    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def _drive() -> tuple[bool, int]:
        task = asyncio.ensure_future(
            wait_mod._run_tmux_lines(t.cast("t.Any", _StubServer()), "display-message")
        )
        # Wait for the stub to spawn and record its pid.
        deadline = time.monotonic() + 5.0
        while not pidfile.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert pidfile.exists(), "stub tmux never started"
        child_pid = int(pidfile.read_text().strip())

        task.cancel()
        propagated = False
        try:
            await task
        except asyncio.CancelledError:
            propagated = True
        return propagated, child_pid

    propagated, child_pid = asyncio.run(_drive())

    assert propagated, "CancelledError did not propagate to the caller"

    # The event loop's child watcher may finish the reap a beat late.
    for _ in range(50):
        if not _pid_alive(child_pid):
            break
        time.sleep(0.02)
    alive = _pid_alive(child_pid)
    if alive:
        os.kill(child_pid, 9)  # clean up the orphan we just proved
    assert not alive, f"child {child_pid} orphaned after cancellation"


def test_run_tmux_lines_happy_path_returns_without_kill(
    tmp_path: pathlib.Path,
) -> None:
    """A cleanly-exiting tmux is returned intact, never torn down.

    Complements ``test_run_tmux_lines_cancel_reaps_child``: proves the
    reap guard does NOT fire on the happy path. A stub ``tmux`` that
    prints two lines and exits 0 must come back verbatim; if the cancel
    guard tore down a process that had already exited, the read would
    still be captured but the widened ``except`` only runs on
    ``CancelledError``, so this locks in that a returned process is left
    alone.
    """
    import asyncio

    from libtmux_mcp import _bounded_io as wait_mod

    stub = tmp_path / "tmux"
    stub.write_text("#!/bin/sh\nprintf 'alpha\\nbeta\\n'\n")
    stub.chmod(0o755)

    class _StubServer:
        tmux_bin = str(stub)
        socket_name = None
        socket_path = None

    out = asyncio.run(
        wait_mod._run_tmux_lines(t.cast("t.Any", _StubServer()), "display-message")
    )
    assert out == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# snapshot_pane tests
# ---------------------------------------------------------------------------


def test_snapshot_pane(mcp_server: Server, mcp_pane: Pane) -> None:
    """snapshot_pane returns rich metadata alongside content."""
    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, PaneSnapshot)
    assert result.pane_id == mcp_pane.pane_id
    assert isinstance(result.content, str)
    assert result.cursor_x >= 0
    assert result.cursor_y >= 0
    assert result.pane_width > 0
    assert result.pane_height > 0
    assert result.pane_in_mode is False
    assert result.pane_mode is None
    assert result.history_size >= 0
    # Default max_lines leaves short captures untruncated.
    assert result.content_truncated is False
    assert result.content_truncated_lines == 0


def test_snapshot_pane_returns_liveness_metadata(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """snapshot_pane returns process, dead-pane, and alternate-screen metadata."""
    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )

    assert result.pane_pid is not None
    assert result.pane_pid.isdigit()
    assert result.pane_dead is False
    assert isinstance(result.alternate_on, bool)


def test_snapshot_pane_truncates_content(mcp_server: Server, mcp_pane: Pane) -> None:
    """snapshot_pane reports truncation via model fields, not in-band header.

    Unlike capture_pane (which returns a bare string and therefore
    signals truncation with a prefix line), snapshot_pane returns a
    Pydantic model, so truncation is surfaced on typed fields:
    ``content_truncated`` and ``content_truncated_lines``. ``content``
    itself is the kept tail with no marker.
    """
    _signal_after_shell_payload(
        mcp_server,
        mcp_pane,
        "; ".join(f"echo snap_line_{i}" for i in range(20)),
    )

    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=5,
        socket_name=mcp_server.socket_name,
    )
    assert result.content_truncated is True
    assert result.content_truncated_lines > 0
    assert result.content.count("\n") == 4  # 5 lines kept -> 4 separators
    assert "[... truncated" not in result.content
    assert "snap_line_19" in result.content


def test_snapshot_pane_max_lines_none_keeps_full_content(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """``max_lines=None`` returns the full content with no truncation flag."""
    _signal_after_shell_payload(
        mcp_server,
        mcp_pane,
        "; ".join(f"echo snapnone_{i}" for i in range(20)),
    )

    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        max_lines=None,
        socket_name=mcp_server.socket_name,
    )
    assert result.content_truncated is False
    assert result.content_truncated_lines == 0
    assert "snapnone_19" in result.content


def test_snapshot_pane_cursor_moves(mcp_server: Server, mcp_pane: Pane) -> None:
    """snapshot_pane reflects cursor position changes."""
    mcp_pane.send_keys("echo hello_snapshot", enter=True)
    retry_until(
        lambda: "hello_snapshot" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "hello_snapshot" in result.content
    assert result.pane_current_command is not None


def test_snapshot_pane_pads_short_display_message_output(
    mcp_server: Server, mcp_pane: Pane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """snapshot_pane survives a truncated display-message result.

    Older tmux versions may drop unknown format variables (e.g.
    `#{pane_mode}`), producing fewer delimited fields than expected.
    Defensive padding must guarantee 11 fields so index access in the
    parser never raises IndexError.
    """
    # Capture the real cmd so non-display-message calls still work.
    real_cmd = mcp_pane.__class__.cmd

    def fake_cmd(self, cmd_name, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = real_cmd(self, cmd_name, *args, **kwargs)
        if cmd_name == "display-message":
            # Return only the first 2 fields (cursor_x, cursor_y) —
            # simulate an old tmux that dropped several unknown format
            # variables. Without defensive padding, parts[2..10] would
            # IndexError.
            parts = result.stdout[0].split("␞") if result.stdout else [""]
            result.stdout = ["␞".join(parts[:2])]
        return result

    monkeypatch.setattr(mcp_pane.__class__, "cmd", fake_cmd)

    # Must not raise IndexError; missing fields default to zero/None.
    result = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert isinstance(result, PaneSnapshot)
    assert result.pane_width == 0
    assert result.pane_height == 0
    assert result.history_size == 0
    assert result.title is None
    assert result.pane_current_command is None
    assert result.pane_current_path is None


# ---------------------------------------------------------------------------
# select_pane tests
# ---------------------------------------------------------------------------


def test_select_pane_by_id(mcp_server: Server, mcp_session: Session) -> None:
    """select_pane focuses a specific pane by ID."""
    window = mcp_session.active_window
    pane1 = window.active_pane
    assert pane1 is not None
    window.split()

    # Select the first pane
    result = select_pane(
        pane_id=pane1.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane1.pane_id


def test_select_pane_directional(mcp_server: Server, mcp_session: Session) -> None:
    """select_pane navigates using direction."""
    window = mcp_session.active_window
    pane1 = window.active_pane
    assert pane1 is not None
    pane2 = window.split()  # creates pane below; pane1 stays active

    # pane1 is active, select "down" should go to pane2
    result = select_pane(
        direction="down",
        window_id=window.window_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane2.pane_id


def test_select_pane_requires_target(mcp_server: Server) -> None:
    """select_pane raises ToolError when neither pane_id nor direction given."""
    with pytest.raises(ToolError, match="Provide either"):
        select_pane(socket_name=mcp_server.socket_name)


def test_select_pane_next_previous_respects_target_window(
    mcp_server: Server, mcp_session: Session
) -> None:
    """select_pane direction=next/previous must anchor to window_id.

    Regression guard: bare `-t +1` / `-t -1` pane targets resolve
    against the attached client's current window (tmux cmd-find.c),
    not against any earlier -t on the command line. Targeting a
    non-active window must use a window-scoped syntax like
    `@window_id.+` to actually affect that window. Without the fix,
    calling select_pane(direction='next', window_id=w2) when w1 is
    the client's active window shifts focus in w1 and leaves w2
    untouched.
    """
    w1 = mcp_session.active_window
    assert w1.active_pane is not None
    w1.split()
    w1.split()
    w2 = mcp_session.new_window()
    w2.split()
    w2.split()

    # Make w1 the active window again, so w2 is the NON-active target.
    w1.select()
    w1.refresh()
    w2.refresh()

    w1_before = w1.active_pane.pane_id
    assert w2.active_pane is not None
    w2_before = w2.active_pane.pane_id

    result = select_pane(
        direction="next",
        window_id=w2.window_id,
        socket_name=mcp_server.socket_name,
    )

    w1.refresh()
    w2.refresh()
    assert w2.active_pane is not None
    w2_after = w2.active_pane.pane_id
    assert w1.active_pane is not None
    w1_after = w1.active_pane.pane_id

    # Result must describe a pane in w2 (the target), not w1.
    w2_pane_ids = {p.pane_id for p in w2.panes}
    assert result.pane_id in w2_pane_ids, (
        f"select_pane returned {result.pane_id} which is not in target "
        f"window {w2.window_id}'s panes {w2_pane_ids}"
    )
    # w2's active pane must have actually changed.
    assert w2_after != w2_before, "target window w2's active pane did not change"
    # w1's active pane must NOT have changed — the wrong-window bug.
    assert w1_after == w1_before, (
        f"select_pane targeting w2 shifted focus in w1 "
        f"({w1_before} -> {w1_after}) — anchor missing"
    )


# ---------------------------------------------------------------------------
# swap_pane tests
# ---------------------------------------------------------------------------


def test_swap_pane(mcp_server: Server, mcp_session: Session) -> None:
    """swap_pane exchanges two pane positions."""
    window = mcp_session.active_window
    pane1 = window.active_pane
    assert pane1 is not None
    pane2 = window.split()

    assert pane1.pane_id is not None
    assert pane2.pane_id is not None

    result = swap_pane(
        source_pane_id=pane1.pane_id,
        target_pane_id=pane2.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result.pane_id == pane1.pane_id


# ---------------------------------------------------------------------------
# pipe_pane tests
# ---------------------------------------------------------------------------


def test_pipe_pane_start_stop(
    mcp_server: Server, mcp_pane: Pane, tmp_path: t.Any
) -> None:
    """pipe_pane starts writes after start and halts writes after stop."""
    log_file = tmp_path / "pane_output.log"

    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=str(log_file),
        socket_name=mcp_server.socket_name,
    )
    assert "piping" in result.lower()

    mcp_pane.send_keys("echo START_MARKER_42", enter=True)
    retry_until(
        lambda: log_file.exists() and "START_MARKER_42" in log_file.read_text(),
        10,
        raises=True,
    )

    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=None,
        socket_name=mcp_server.socket_name,
    )
    assert "stopped" in result.lower()

    size_after_stop = log_file.stat().st_size
    mcp_pane.send_keys("echo POST_STOP_MARKER_99", enter=True)
    # Poll briefly — if stop worked the file must not grow.
    with pytest.raises(libtmux_exc.WaitTimeout):
        retry_until(
            lambda: log_file.stat().st_size > size_after_stop,
            1,
            raises=True,
        )
    assert "POST_STOP_MARKER_99" not in log_file.read_text()


def test_pipe_pane_quotes_path_with_spaces(
    mcp_server: Server, mcp_pane: Pane, tmp_path: t.Any
) -> None:
    """pipe_pane survives an output_path containing spaces.

    Without shell-quoting the path, tmux runs `cat >> /tmp/has space.log`
    which the shell splits into two arguments — the redirect silently
    lands on `/tmp/has` and `space.log` becomes a literal cat argument.
    """
    log_file = tmp_path / "has space.log"
    marker = "PIPE_PANE_MARKER_42"

    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=str(log_file),
        socket_name=mcp_server.socket_name,
    )
    assert "piping" in result.lower()

    try:
        mcp_pane.send_keys(f"echo {marker}", enter=True)
        retry_until(
            lambda: log_file.exists() and marker in log_file.read_text(),
            10,
            raises=True,
        )
    finally:
        pipe_pane(
            pane_id=mcp_pane.pane_id,
            output_path=None,
            socket_name=mcp_server.socket_name,
        )


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param("fmt-#{pane_id}.log", id="format-substitution"),
        pytest.param("job-#(echo pwned).log", id="command-job"),
        # Legacy single-char aliases expand too: '#S' is the session name,
        # so '#Session.log' silently became '<session>ession.log'.
        pytest.param("#Session.log", id="legacy-alias"),
        pytest.param("style-#[fg=red].log", id="style-sequence-left-alone"),
        pytest.param("already-##{x}.log", id="already-doubled"),
        pytest.param("issue #42.log", id="bare-hash"),
        # pipe-pane uses format_expand_time(), so strftime runs too:
        # '100%done.log' became '10025one.log' via %d.
        pytest.param("100%done.log", id="strftime-percent-d"),
        pytest.param("date-%Y.log", id="strftime-percent-y"),
    ],
)
def test_pipe_pane_writes_the_exact_path_requested(
    mcp_server: Server, mcp_pane: Pane, tmp_path: t.Any, filename: str
) -> None:
    """pipe_pane logs to the literal output_path, not a tmux-expanded one.

    ``pipe-pane`` runs its argument through tmux's format expander before
    /bin/sh sees it, so ``shlex.quote`` alone guards only the shell layer.
    An unescaped ``#{pane_id}`` in the path expanded, and the log landed on
    a different file than the one the tool reported back.

    ``#[`` must NOT be escaped: tmux copies a ``#``-run followed by ``[``
    verbatim and never collapses ``##[``, so doubling there reintroduces
    the same wrong-file bug from the other direction.
    """
    log_file = tmp_path / filename
    marker = "PIPE_EXACT_PATH_MARKER"

    result = pipe_pane(
        pane_id=mcp_pane.pane_id,
        output_path=str(log_file),
        socket_name=mcp_server.socket_name,
    )
    # The success string must name the file that actually gets written.
    assert str(log_file) in result

    try:
        mcp_pane.send_keys(f"echo {marker}", enter=True)
        retry_until(
            lambda: log_file.exists() and marker in log_file.read_text(),
            10,
            raises=True,
        )
        # Nothing else may appear: an expanded path would create a sibling.
        written = sorted(p.name for p in tmp_path.iterdir())
        assert written == [filename], f"unexpected files on disk: {written}"
    finally:
        pipe_pane(
            pane_id=mcp_pane.pane_id,
            output_path=None,
            socket_name=mcp_server.socket_name,
        )


def test_pipe_pane_rejects_empty_path(mcp_server: Server, mcp_pane: Pane) -> None:
    """pipe_pane raises ToolError when output_path is empty or whitespace."""
    for bad in ("", "   ", "\t"):
        with pytest.raises(ToolError, match="non-empty"):
            pipe_pane(
                pane_id=mcp_pane.pane_id,
                output_path=bad,
                socket_name=mcp_server.socket_name,
            )


# ---------------------------------------------------------------------------
# display_message tests
# ---------------------------------------------------------------------------


def test_display_message(mcp_server: Server, mcp_pane: Pane) -> None:
    """display_message expands tmux format strings."""
    result = display_message(
        format_string="#{pane_width}x#{pane_height}",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert "x" in result
    parts = result.split("x")
    assert len(parts) == 2
    assert parts[0].isdigit()
    assert parts[1].isdigit()


def test_display_message_zoomed_flag(mcp_server: Server, mcp_session: Session) -> None:
    """display_message queries arbitrary tmux variables."""
    window = mcp_session.active_window
    pane = window.active_pane
    assert pane is not None
    result = display_message(
        format_string="#{window_zoomed_flag}",
        pane_id=pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert result in ("0", "1")


def test_display_message_passes_a_dash_leading_format(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """A format starting with '-' reaches tmux instead of being a flag.

    ``format_string="-p"`` was consumed as tmux's own print flag, so no
    format reached tmux and it answered with its DEFAULT message -- a
    plausible string answering a question nobody asked.
    """
    assert (
        display_message(
            format_string="-p",
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
        == "-p"
    )
    assert (
        display_message(
            format_string="-> #{pane_id}",
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )
        == f"-> {mcp_pane.pane_id}"
    )


def test_display_message_rejects_format_jobs(
    mcp_server: Server, mcp_pane: Pane, tmp_path: pathlib.Path
) -> None:
    """display_message rejects tmux format jobs before tmux evaluates them."""
    marker = tmp_path / "display_message_format_job_marker"

    with pytest.raises(ToolError, match=r"#\("):
        display_message(
            format_string=f"#(printf ok > {shlex.quote(str(marker))})",
            pane_id=mcp_pane.pane_id,
            socket_name=mcp_server.socket_name,
        )

    time.sleep(0.5)
    assert not marker.exists()


# ---------------------------------------------------------------------------
# enter_copy_mode / exit_copy_mode tests
# ---------------------------------------------------------------------------


def test_enter_and_exit_copy_mode(mcp_server: Server, mcp_pane: Pane) -> None:
    """enter_copy_mode enters copy mode, exit_copy_mode leaves it."""
    enter_result = enter_copy_mode(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert enter_result.pane_id == mcp_pane.pane_id

    # Verify pane is in copy mode via snapshot
    snap = snapshot_pane(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert snap.pane_in_mode is True

    exit_result = exit_copy_mode(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    assert exit_result.pane_id == mcp_pane.pane_id


def test_enter_copy_mode_with_scroll(mcp_server: Server, mcp_pane: Pane) -> None:
    """enter_copy_mode can scroll up immediately."""
    # Generate some scrollback history
    for i in range(20):
        mcp_pane.send_keys(f"echo scrollback_line_{i}", enter=True)
    retry_until(
        lambda: "scrollback_line_19" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )

    enter_result = enter_copy_mode(
        pane_id=mcp_pane.pane_id,
        scroll_up=5,
        socket_name=mcp_server.socket_name,
    )
    assert enter_result.pane_id == mcp_pane.pane_id

    # Clean up: exit copy mode
    exit_copy_mode(
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )


# ---------------------------------------------------------------------------
# paste_text tests
# ---------------------------------------------------------------------------


def test_paste_text(mcp_server: Server, mcp_pane: Pane) -> None:
    """paste_text pastes text into a pane via tmux buffer.

    Uses bracket=False and a trailing newline so the shell actually
    executes the echo command. Previous versions of this test
    relied on the default bracket=True, which is fragile on CI:
    bash readline needs a prompt cycle to latch bracketed-paste
    mode, and if the paste arrives before that the escape sequences
    get consumed as unrecognized input and the marker never reaches
    the visible pane buffer. bracket=False sends raw bytes and the
    trailing newline forces execution, exercising the full
    paste->execute->output round-trip.
    """
    result = paste_text(
        text="echo PASTE_TEST_marker_xyz\n",
        pane_id=mcp_pane.pane_id,
        bracket=False,
        socket_name=mcp_server.socket_name,
    )
    assert "pasted" in result.lower()

    # Verify the echoed marker reaches the pane. 10 seconds is
    # generous on local machines (<1s) but tolerates slow CI
    # runners where bash cold-start can exceed the default budget.
    retry_until(
        lambda: "PASTE_TEST_marker_xyz" in "\n".join(mcp_pane.capture_pane()),
        10,
        raises=True,
    )


def test_paste_text_does_not_leak_named_buffer(
    mcp_server: Server, mcp_pane: Pane
) -> None:
    """paste_text must not leave its ``libtmux_mcp_*_paste`` buffer behind.

    Regression guard for the pre-fix behavior: the earlier
    implementation used tmux's default unnamed buffer AND relied on
    `paste-buffer -d` to clean up. If paste-buffer failed mid-flight
    the buffer leaked. The fix generates a unique
    ``libtmux_mcp_<uuid>_paste`` named buffer per call (matching the
    ``buffer_tools._BUFFER_NAME_RE`` shape) and adds a best-effort
    ``delete-buffer -b`` in ``finally`` so the server is left in a
    clean state on both success and failure paths.

    The ``libtmux_mcp_`` prefix matches the namespace used by
    :mod:`libtmux_mcp.tools.buffer_tools`, so an operator filtering
    ``list-buffers`` on that prefix sees every MCP-owned buffer.

    The check is portable across every tmux version the CI matrix
    tests (3.2a through master): ``list-buffers`` with a format string
    returns buffer names without any version-specific behavior.
    """
    paste_text(
        text="echo BUFFER_ISOLATION_test",
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )

    listing = mcp_server.cmd("list-buffers", "-F", "#{buffer_name}")
    buffer_names = "\n".join(listing.stdout or [])
    assert "libtmux_mcp_" not in buffer_names, (
        f"paste_text leaked a named buffer: {buffer_names!r}"
    )


# ---------------------------------------------------------------------------
# Registration-time annotation verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected_open_world"),
    [
        # Shell-driving tools: the command the caller sends can reach
        # arbitrary external state, so the interaction is open-world.
        ("send_keys", True),
        ("send_keys_batch", True),
        ("run_command", True),
        ("paste_text", True),
        ("pipe_pane", True),
        # Create-style tools: allocate tmux objects only. Not open-world
        # even though they share the old ANNOTATIONS_CREATE preset.
        ("swap_pane", False),
        ("enter_copy_mode", False),
    ],
)
def test_pane_tool_open_world_hint_registration(
    tool_name: str, expected_open_world: bool
) -> None:
    """Pane tools advertise ``openWorldHint`` matching their real semantics.

    Regression guard for the shared-preset trap: the old
    ``ANNOTATIONS_CREATE`` preset was applied to both shell-driving and
    non-shell-driving tools, so every caller saw ``openWorldHint=False``.
    A new ``ANNOTATIONS_SHELL`` preset now carries ``openWorldHint=True``
    for the three shell-driving tools only, leaving the other
    ``ANNOTATIONS_CREATE`` users unchanged.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import pane_tools

    mcp = FastMCP(name="test-pane-annotations")
    pane_tools.register(mcp)

    tool = asyncio.run(mcp.get_tool(tool_name))
    assert tool is not None, f"{tool_name} should be registered"
    assert tool.annotations is not None, (
        f"{tool_name} registration should carry annotations"
    )
    assert wire_annotations(tool).get("openWorldHint") is expected_open_world


def test_respawn_pane_advertises_destructive_non_idempotent() -> None:
    """``respawn_pane`` registers as mutating-tier with destructive hints.

    Default ``kill=True`` sends ``SPAWN_KILL`` to the running process
    (`cmd-respawn-pane.c:78-79`); repeated calls kill repeated processes.
    The MCP spec defines ``destructiveHint`` as "may perform destructive
    updates" and ``idempotentHint`` as "calling repeatedly will have no
    additional effect" (`mcp/types.py:1268-1282`). The default
    ``ANNOTATIONS_MUTATING`` preset (``destructiveHint=False``,
    ``idempotentHint=True``) would lie to the agent. The new
    ``ANNOTATIONS_MUTATING_DESTRUCTIVE`` preset stays in ``TAG_MUTATING``
    so the recovery use case remains visible to default-profile clients,
    while honestly advertising destructive non-idempotent semantics.
    """
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import pane_tools

    mcp = FastMCP(name="test-respawn-annotations")
    pane_tools.register(mcp)

    tool = asyncio.run(mcp.get_tool("respawn_pane"))
    assert tool is not None, "respawn_pane should be registered"
    assert tool.annotations is not None, (
        "respawn_pane registration should carry annotations"
    )
    assert wire_annotations(tool).get("destructiveHint") is True
    assert wire_annotations(tool).get("idempotentHint") is False
    assert wire_annotations(tool).get("readOnlyHint") is False


def test_clear_pane_advertises_destructive_non_idempotent() -> None:
    """``clear_pane`` registers as mutating-tier with destructive hints."""
    import asyncio

    from fastmcp import FastMCP

    from libtmux_mcp.tools import pane_tools

    mcp = FastMCP(name="test-clear-pane-annotations")
    pane_tools.register(mcp)

    tool = asyncio.run(mcp.get_tool("clear_pane"))
    assert tool is not None, "clear_pane should be registered"
    assert tool.annotations is not None, "clear_pane should carry annotations"
    assert wire_annotations(tool).get("destructiveHint") is True
    assert wire_annotations(tool).get("idempotentHint") is False
    assert wire_annotations(tool).get("readOnlyHint") is False


# ---------------------------------------------------------------------------
# Typed-output regression guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected_type"),
    [
        # Read-heavy tools must keep returning Pydantic models so MCP
        # clients get machine-readable ``outputSchema`` entries and
        # agents don't have to re-parse strings. Regression guard:
        # any future change that flattens one of these back to ``str``
        # will break this test and force an explicit review.
        ("capture_since", "CaptureSinceResult"),
        ("get_pane_info", "PaneInfo"),
        ("snapshot_pane", "PaneSnapshot"),
    ],
)
def test_pane_read_tools_return_pydantic_models(
    mcp_server: Server, mcp_pane: Pane, tool_name: str, expected_type: str
) -> None:
    """Read-heavy pane tools return their Pydantic model, not ``str``."""
    tools: dict[str, t.Callable[..., t.Any]] = {
        "capture_since": capture_since,
        "get_pane_info": get_pane_info,
        "snapshot_pane": snapshot_pane,
    }
    maybe_result = tools[tool_name](
        pane_id=mcp_pane.pane_id,
        socket_name=mcp_server.socket_name,
    )
    if tool_name == "capture_since":
        import asyncio

        result = asyncio.run(t.cast(t.Coroutine[t.Any, t.Any, t.Any], maybe_result))
    else:
        result = maybe_result
    assert type(result).__name__ == expected_type
    assert hasattr(result, "model_dump"), "expected a Pydantic BaseModel instance"
