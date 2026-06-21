"""Tests for typed tmux operation chains."""

from __future__ import annotations

import asyncio
import time
import typing as t

import pytest
from libtmux._experimental.chain import CommandScopeError
from pydantic import ValidationError

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.models import (
    CapturePaneOperation,
    CapturePaneStepResult,
    KillPaneOperation,
    MakeGridOperation,
    PaneIdTarget,
    RefTarget,
    RunTmuxPlanResult,
    SetOptionOperation,
    SplitEvenlyOperation,
    SplitPaneOperation,
    SplitPaneStepResult,
    TmuxOperation,
    TmuxOperationStatus,
    TmuxSendKeysOperation,
)
from libtmux_mcp.tools import chain_tools
from libtmux_mcp.tools.chain_tools import (
    TMUX_OPERATIONS_ADAPTER,
    run_tmux_plan,
)

if t.TYPE_CHECKING:
    import pathlib

    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session


def _pane_target(pane: Pane) -> PaneIdTarget:
    """Return a typed pane-id target for a fixture pane."""
    assert pane.pane_id is not None
    return PaneIdTarget(pane_id=pane.pane_id)


def test_run_tmux_operations_runs_each_operation(
    mcp_session: Session,
) -> None:
    """Each operation runs and reports its own typed status."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SetOptionOperation(option="@cc_ops_a", value="1", global_=True),
                SetOptionOperation(option="@cc_ops_b", value="2", global_=True),
            ],
            socket_name=server.socket_name,
        ),
    )

    assert result.succeeded
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.SUCCEEDED,
        TmuxOperationStatus.SUCCEEDED,
    ]
    assert result.diagnostics is None
    assert server.cmd("show-option", "-gv", "@cc_ops_a").stdout == ["1"]
    assert server.cmd("show-option", "-gv", "@cc_ops_b").stdout == ["2"]


def test_run_tmux_operations_explain_attaches_diagnostics(
    mcp_session: Session,
) -> None:
    """``explain`` attaches one per-operation dispatch record."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SetOptionOperation(option="@cc_ops_x", value="1", global_=True),
                SetOptionOperation(option="@cc_ops_y", value="2", global_=True),
            ],
            explain=True,
            socket_name=server.socket_name,
        ),
    )

    assert result.succeeded
    assert result.diagnostics is not None
    assert result.diagnostics.dispatch_count == 2
    assert [dispatch.index for dispatch in result.diagnostics.dispatches] == [0, 1]
    assert all(
        dispatch.argv[0] == "set-option" for dispatch in result.diagnostics.dispatches
    )


def test_run_tmux_operations_capture_returns_lines(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """A read operation returns its own captured lines on its own step."""
    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = "cc_ops_capture"
    mcp_pane.send_keys(f"printf 'CC_OPS_CAPTURE\\n'; tmux wait-for -S {channel}")
    asyncio.run(
        wait_for_channel(channel, timeout=5.0, socket_name=mcp_server.socket_name)
    )

    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SetOptionOperation(
                    option="@cc_ops_before_capture",
                    value="1",
                    global_=True,
                ),
                CapturePaneOperation(target=_pane_target(mcp_pane)),
            ],
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    capture = result.steps[1]
    assert isinstance(capture, CapturePaneStepResult)
    assert capture.lines is not None
    assert "CC_OPS_CAPTURE" in "\n".join(capture.lines)


def test_run_tmux_operations_captures_split_refs(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """A typed split ref can target later operations without raw commands."""
    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = "cc_ops_split_ref"
    keys = f"printf 'CC_OPS_REF\\n'; tmux wait-for -S {channel}"
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SplitPaneOperation(
                    ref="child",
                    target=_pane_target(mcp_pane),
                ),
                TmuxSendKeysOperation(target=RefTarget(ref="child"), keys=keys),
            ],
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    split = result.steps[0]
    assert isinstance(split, SplitPaneStepResult)
    new_pane_id = result.created_panes["child"]
    assert new_pane_id.startswith("%")
    assert split.pane_id == new_pane_id

    asyncio.run(
        wait_for_channel(channel, timeout=5.0, socket_name=mcp_server.socket_name)
    )
    mcp_pane.window.refresh()
    new_pane = mcp_pane.window.panes.get(pane_id=new_pane_id)
    assert new_pane is not None
    assert "CC_OPS_REF" in "\n".join(new_pane.capture_pane())


def test_run_tmux_plan_split_evenly(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """split_evenly creates an even row/column of the requested pane count."""
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SplitEvenlyOperation(
                    target=_pane_target(mcp_pane),
                    count=3,
                    axis="horizontal",
                ),
            ],
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    assert result.steps[0].status == TmuxOperationStatus.SUCCEEDED
    mcp_pane.window.refresh()
    assert len(mcp_pane.window.panes) == 3


def test_run_tmux_plan_make_grid(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """make_grid tiles a pane's window into rows * cols panes."""
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                MakeGridOperation(target=_pane_target(mcp_pane), rows=2, cols=2),
            ],
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    mcp_pane.window.refresh()
    assert len(mcp_pane.window.panes) == 4


def test_run_tmux_plan_kill_pane_requires_destructive_tier(
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kill_pane fails closed unless the server runs at the destructive tier."""
    monkeypatch.setenv("LIBTMUX_SAFETY", "mutating")
    result = asyncio.run(
        run_tmux_plan(
            operations=[KillPaneOperation(target=PaneIdTarget(pane_id="%999999"))],
            explain=True,
            socket_name=mcp_session.server.socket_name,
        ),
    )

    assert not result.succeeded
    assert result.diagnostics is not None
    assert result.diagnostics.dispatch_count == 0
    assert result.steps[0].status == TmuxOperationStatus.FAILED
    assert result.steps[0].error == "kill_pane requires the destructive safety tier"


def test_run_tmux_plan_kill_pane_at_destructive_tier(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kill_pane removes a pane when the server runs at the destructive tier."""
    monkeypatch.setenv("LIBTMUX_SAFETY", "destructive")
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SplitPaneOperation(ref="child", target=_pane_target(mcp_pane)),
                KillPaneOperation(target=RefTarget(ref="child")),
            ],
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    new_pane_id = result.created_panes["child"]
    mcp_pane.window.refresh()
    pane_ids = [pane.pane_id for pane in mcp_pane.window.panes]
    assert new_pane_id not in pane_ids


def test_run_tmux_operations_continue_runs_later_ops(
    mcp_session: Session,
) -> None:
    """Continue mode records each failure and runs the rest."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                TmuxSendKeysOperation(
                    target=PaneIdTarget(pane_id="%999999"),
                    keys="bad",
                    enter=False,
                ),
                SetOptionOperation(
                    option="@cc_ops_after_error",
                    value="set",
                    global_=True,
                ),
            ],
            on_error="continue",
            socket_name=server.socket_name,
        ),
    )

    assert not result.succeeded
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.FAILED,
        TmuxOperationStatus.SUCCEEDED,
    ]
    assert server.cmd("show-option", "-gv", "@cc_ops_after_error").stdout == ["set"]


def test_run_tmux_operations_stop_halts_after_failure(
    mcp_session: Session,
) -> None:
    """Stop mode (the default) skips every operation after the first failure."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SetOptionOperation(option="@cc_ops_cm_a", value="1", global_=True),
                TmuxSendKeysOperation(
                    target=PaneIdTarget(pane_id="%999999"),
                    keys="bad",
                    enter=False,
                ),
                SetOptionOperation(option="@cc_ops_cm_b", value="2", global_=True),
            ],
            socket_name=server.socket_name,
        ),
    )

    assert not result.succeeded
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.SUCCEEDED,
        TmuxOperationStatus.FAILED,
        TmuxOperationStatus.SKIPPED,
    ]
    assert result.steps[1].error is not None
    assert "%999999" in result.steps[1].error
    # The first op ran; the op after the failure never dispatched.
    assert server.cmd("show-option", "-gv", "@cc_ops_cm_a").stdout == ["1"]
    assert server.cmd("show-option", "-gv", "@cc_ops_cm_b").stdout == []


def test_run_tmux_operations_split_inherits_target_directory(
    mcp_session: Session,
    tmp_path: pathlib.Path,
) -> None:
    """A split's new pane inherits the split target's working directory."""
    server = mcp_session.server
    target_dir = str(tmp_path)
    created = server.cmd(
        "new-window",
        "-t",
        mcp_session.session_id,
        "-P",
        "-F",
        "#{pane_id}",
        "-c",
        target_dir,
    )
    target_pane_id = created.stdout[0]
    target_cwd = server.cmd(
        "display-message",
        "-t",
        target_pane_id,
        "-p",
        "#{pane_current_path}",
    ).stdout

    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SplitPaneOperation(
                    ref="child",
                    target=PaneIdTarget(pane_id=target_pane_id),
                ),
            ],
            socket_name=server.socket_name,
        ),
    )

    assert result.succeeded
    new_pane_id = result.created_panes["child"]
    new_cwd = server.cmd(
        "display-message",
        "-t",
        new_pane_id,
        "-p",
        "#{pane_current_path}",
    ).stdout
    assert new_cwd == target_cwd


def test_run_tmux_operations_surfaces_libtmux_scope_error(
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compiler reports a libtmux scope-contract failure as a step failure.

    The contract metadata is static, so this uses monkeypatch instead of a
    tmux fixture to simulate libtmux rejecting a command's target scope.
    """

    def fail_scope(command_name: str, target_scope: str) -> None:
        msg = f"{command_name} {target_scope} wrong scope from test"
        raise CommandScopeError(msg)

    monkeypatch.setattr(
        chain_tools,
        "validate_command_scope",
        fail_scope,
        raising=False,
    )

    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SetOptionOperation(
                    option="@cc_ops_contract_error",
                    value="set",
                    global_=True,
                ),
            ],
            explain=True,
            socket_name=mcp_session.server.socket_name,
        ),
    )

    assert not result.succeeded
    assert result.diagnostics is not None
    assert result.diagnostics.dispatch_count == 0
    assert result.steps[0].status == TmuxOperationStatus.FAILED
    assert result.steps[0].error is not None
    assert "wrong scope from test" in result.steps[0].error


def test_run_tmux_operations_dry_run_plans_without_mutating(
    mcp_session: Session,
) -> None:
    """Dry-run returns planned steps without changing tmux state."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SetOptionOperation(option="@cc_ops_dry_a", value="1", global_=True),
                SetOptionOperation(option="@cc_ops_dry_b", value="2", global_=True),
            ],
            dry_run=True,
            explain=True,
            socket_name=server.socket_name,
        ),
    )

    assert result.succeeded
    assert result.dry_run
    assert result.diagnostics is not None
    assert result.diagnostics.dispatch_count == 2
    assert all(
        dispatch.returncode is None for dispatch in result.diagnostics.dispatches
    )
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.PLANNED,
        TmuxOperationStatus.PLANNED,
    ]
    for option in ("@cc_ops_dry_a", "@cc_ops_dry_b"):
        assert server.cmd("show-option", "-gv", option).stdout == []


def test_run_tmux_operations_dry_run_plans_split_ref(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """Dry-run uses placeholders for pane refs without creating panes."""
    mcp_pane.window.refresh()
    pane_count = len(mcp_pane.window.panes)

    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SplitPaneOperation(
                    ref="child",
                    target=_pane_target(mcp_pane),
                ),
                TmuxSendKeysOperation(
                    target=RefTarget(ref="child"),
                    keys="printf 'DRY_RUN_REF\\n'",
                ),
            ],
            dry_run=True,
            socket_name=mcp_server.socket_name,
        ),
    )

    placeholder = "<pane_ref:child>"
    assert result.succeeded
    assert result.dry_run
    assert result.created_panes == {"child": placeholder}
    split = result.steps[0]
    assert isinstance(split, SplitPaneStepResult)
    assert split.status == TmuxOperationStatus.PLANNED
    assert split.pane_id == placeholder
    assert result.steps[1].status == TmuxOperationStatus.PLANNED

    mcp_pane.window.refresh()
    assert len(mcp_pane.window.panes) == pane_count


def test_run_tmux_operations_dry_run_plans_output_ops(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """Dry-run plans read operations as planned steps."""
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SetOptionOperation(
                    option="@cc_ops_dry_pending",
                    value="1",
                    global_=True,
                ),
                CapturePaneOperation(target=_pane_target(mcp_pane)),
            ],
            dry_run=True,
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.PLANNED,
        TmuxOperationStatus.PLANNED,
    ]


def test_run_tmux_operations_dispatch_timeout(
    mcp_server: Server,
    mcp_pane: Pane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch timeout returns a failed per-operation result.

    The dispatch helper is a synchronous wrapper around tmux, so this uses
    monkeypatch rather than a blocking tmux command.
    """

    def sleep_dispatch(*args: object, **kwargs: object) -> t.NoReturn:
        time.sleep(0.05)
        msg = "dispatch should have timed out"
        raise AssertionError(msg)

    monkeypatch.setattr(chain_tools, "_dispatch_standalone", sleep_dispatch)
    assert mcp_pane.pane_id is not None

    result = asyncio.run(
        run_tmux_plan(
            operations=[CapturePaneOperation(target=_pane_target(mcp_pane))],
            dispatch_timeout=0.001,
            explain=True,
            socket_name=mcp_server.socket_name,
        ),
    )

    assert not result.succeeded
    assert result.diagnostics is not None
    assert result.diagnostics.dispatch_count == 1
    assert result.diagnostics.dispatches[0].index == 0
    assert result.diagnostics.dispatches[0].returncode is None
    assert result.diagnostics.dispatches[0].stderr == [
        "tmux dispatch timed out after 0.001 seconds",
    ]
    assert result.steps[0].status == TmuxOperationStatus.FAILED
    assert result.steps[0].error == "tmux dispatch timed out after 0.001 seconds"


class TimeoutValidationCase(t.NamedTuple):
    """Case for timeout input validation."""

    test_id: str
    dispatch_timeout: float


@pytest.mark.parametrize(
    "case",
    [
        TimeoutValidationCase(test_id="zero", dispatch_timeout=0.0),
        TimeoutValidationCase(test_id="negative", dispatch_timeout=-1.0),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_dispatch_timeout_validation(
    case: TimeoutValidationCase,
    mcp_session: Session,
) -> None:
    """Dispatch timeout must be positive when set."""
    with pytest.raises(ExpectedToolError, match="dispatch_timeout"):
        asyncio.run(
            run_tmux_plan(
                operations=[
                    SetOptionOperation(
                        option="@cc_ops_timeout_validation",
                        value="1",
                        global_=True,
                    ),
                ],
                dispatch_timeout=case.dispatch_timeout,
                socket_name=mcp_session.server.socket_name,
            ),
        )


class CompileErrorPathCase(t.NamedTuple):
    """Case for branch-local compile error paths."""

    test_id: str
    operations: list[TmuxOperation]
    expected_statuses: list[TmuxOperationStatus]
    expected_error: str | None


@pytest.mark.parametrize(
    "case",
    [
        CompileErrorPathCase(
            test_id="unknown_ref",
            operations=[
                TmuxSendKeysOperation(
                    target=RefTarget(ref="missing"),
                    keys="bad",
                    enter=False,
                ),
            ],
            expected_statuses=[TmuxOperationStatus.FAILED],
            expected_error="unknown ref: missing",
        ),
        CompileErrorPathCase(
            test_id="failure_before_compile_error",
            operations=[
                TmuxSendKeysOperation(
                    target=PaneIdTarget(pane_id="%999999"),
                    keys="bad",
                    enter=False,
                ),
                TmuxSendKeysOperation(
                    target=RefTarget(ref="missing"),
                    keys="bad",
                    enter=False,
                ),
            ],
            expected_statuses=[
                TmuxOperationStatus.FAILED,
                TmuxOperationStatus.SKIPPED,
            ],
            expected_error=None,
        ),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_compile_error_paths(
    case: CompileErrorPathCase,
    mcp_session: Session,
) -> None:
    """Compile errors report directly; stop mode skips operations after them."""
    result = asyncio.run(
        run_tmux_plan(
            operations=case.operations,
            socket_name=mcp_session.server.socket_name,
        ),
    )

    assert not result.succeeded
    assert [step.status for step in result.steps] == case.expected_statuses
    if case.expected_error is not None:
        assert result.steps[0].error == case.expected_error


def test_run_tmux_operations_split_failure_skips_later_ops(
    mcp_session: Session,
) -> None:
    """A failed split skips every later operation under stop mode."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_plan(
            operations=[
                SplitPaneOperation(
                    ref="child",
                    target=PaneIdTarget(pane_id="%999999"),
                ),
                TmuxSendKeysOperation(
                    target=RefTarget(ref="child"),
                    keys="bad",
                    enter=False,
                ),
                SetOptionOperation(
                    option="@cc_ops_after_split_failure",
                    value="set",
                    global_=True,
                ),
            ],
            socket_name=server.socket_name,
        ),
    )

    assert not result.succeeded
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.FAILED,
        TmuxOperationStatus.SKIPPED,
        TmuxOperationStatus.SKIPPED,
    ]
    assert server.cmd("show-option", "-gv", "@cc_ops_after_split_failure").stdout == []


class RollbackCase(t.NamedTuple):
    """Case for rollback of created panes."""

    test_id: str
    rollback_on_error: bool
    expect_rollback: bool


@pytest.mark.parametrize(
    "case",
    [
        RollbackCase(
            test_id="enabled",
            rollback_on_error=True,
            expect_rollback=True,
        ),
        RollbackCase(
            test_id="disabled",
            rollback_on_error=False,
            expect_rollback=False,
        ),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_rolls_back_created_panes(
    case: RollbackCase,
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """Rollback kills panes created before a later operation fails."""
    result: RunTmuxPlanResult | None = None
    try:
        result = asyncio.run(
            run_tmux_plan(
                operations=[
                    SplitPaneOperation(
                        ref="child",
                        target=_pane_target(mcp_pane),
                    ),
                    TmuxSendKeysOperation(
                        target=PaneIdTarget(pane_id="%999999"),
                        keys="bad",
                        enter=False,
                    ),
                ],
                rollback_on_error=case.rollback_on_error,
                socket_name=mcp_server.socket_name,
            ),
        )

        assert not result.succeeded
        new_pane_id = result.created_panes["child"]
        assert result.rollback_errors == []
        assert result.rolled_back_panes == (
            [new_pane_id] if case.expect_rollback else []
        )
        mcp_pane.window.refresh()
        pane_ids = [pane.pane_id for pane in mcp_pane.window.panes]
        assert (new_pane_id not in pane_ids) is case.expect_rollback
    finally:
        if result is not None and not case.expect_rollback:
            pane_id = result.created_panes.get("child")
            if pane_id is not None:
                mcp_server.cmd("kill-pane", "-t", pane_id)


class ValidationCase(t.NamedTuple):
    """Case for typed operation validation failures."""

    test_id: str
    operations: object
    expected_error: type[Exception]


@pytest.mark.parametrize(
    "case",
    [
        ValidationCase(
            test_id="empty_operations",
            operations=[],
            expected_error=ExpectedToolError,
        ),
        ValidationCase(
            test_id="unknown_raw_kind",
            operations=[{"kind": "kill_server"}],
            expected_error=ValidationError,
        ),
        ValidationCase(
            test_id="unknown_target_kind",
            operations=[
                {"kind": "send_keys", "keys": "x", "target": {"kind": "bogus"}}
            ],
            expected_error=ValidationError,
        ),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_validation(
    case: ValidationCase,
    mcp_session: Session,
) -> None:
    """The tool accepts only non-empty typed operation variants."""
    if case.expected_error is ValidationError:
        with pytest.raises(case.expected_error):
            TMUX_OPERATIONS_ADAPTER.validate_python(case.operations)
        return

    with pytest.raises(case.expected_error):
        asyncio.run(
            run_tmux_plan(
                operations=t.cast("list[TmuxOperation]", case.operations),
                socket_name=mcp_session.server.socket_name,
            ),
        )
