"""Tests for typed tmux operation chains."""

from __future__ import annotations

import asyncio
import typing as t

import pytest
from libtmux._experimental.chain import ChainabilityError, CommandScopeError
from pydantic import ValidationError

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.models import (
    CapturePaneOperation,
    SetOptionOperation,
    SplitPaneOperation,
    TmuxOperation,
    TmuxOperationStatus,
    TmuxSendKeysOperation,
)
from libtmux_mcp.tools import chain_tools
from libtmux_mcp.tools.chain_tools import (
    TMUX_OPERATIONS_ADAPTER,
    run_tmux_operations,
)

if t.TYPE_CHECKING:
    from libtmux.pane import Pane
    from libtmux.server import Server
    from libtmux.session import Session


class SetOptionChainCase(t.NamedTuple):
    """Case for option operations that can fold into one dispatch."""

    test_id: str
    operations: list[TmuxOperation]
    expected_values: dict[str, str]


@pytest.mark.parametrize(
    "case",
    [
        SetOptionChainCase(
            test_id="two_global_options",
            operations=[
                SetOptionOperation(option="@cc_ops_a", value="1", global_=True),
                SetOptionOperation(option="@cc_ops_b", value="2", global_=True),
            ],
            expected_values={"@cc_ops_a": "1", "@cc_ops_b": "2"},
        ),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_folds_chainable_ops(
    case: SetOptionChainCase,
    mcp_session: Session,
) -> None:
    """Consecutive no-output mutating operations use one native chain."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_operations(
            operations=case.operations,
            socket_name=server.socket_name,
        ),
    )

    assert result.succeeded
    assert result.dispatch_count == 1
    assert result.dispatches[0].mode == "chain"
    assert ";" in result.dispatches[0].argv
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.SUCCEEDED,
        TmuxOperationStatus.SUCCEEDED,
    ]
    for option, value in case.expected_values.items():
        assert server.cmd("show-option", "-gv", option).stdout == [value]


def test_run_tmux_operations_breaks_before_output_op(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """Read operations force a standalone dispatch with per-step stdout."""
    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = "cc_ops_capture"
    mcp_pane.send_keys(f"printf 'CC_OPS_CAPTURE\\n'; tmux wait-for -S {channel}")
    asyncio.run(
        wait_for_channel(channel, timeout=5.0, socket_name=mcp_server.socket_name)
    )

    result = asyncio.run(
        run_tmux_operations(
            operations=[
                SetOptionOperation(
                    option="@cc_ops_before_capture",
                    value="1",
                    global_=True,
                ),
                CapturePaneOperation(pane_id=mcp_pane.pane_id),
            ],
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    assert result.dispatch_count == 2
    assert [dispatch.mode for dispatch in result.dispatches] == [
        "chain",
        "standalone",
    ]
    assert result.steps[1].stdout is not None
    assert "CC_OPS_CAPTURE" in "\n".join(result.steps[1].stdout)


def test_run_tmux_operations_captures_split_refs(
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """A typed split ref can target later operations without raw commands."""
    from libtmux_mcp.tools.wait_for_tools import wait_for_channel

    channel = "cc_ops_split_ref"
    keys = f"printf 'CC_OPS_REF\\n'; tmux wait-for -S {channel}"
    result = asyncio.run(
        run_tmux_operations(
            operations=[
                SplitPaneOperation(ref="child", pane_id=mcp_pane.pane_id),
                TmuxSendKeysOperation(pane_ref="child", keys=keys),
            ],
            socket_name=mcp_server.socket_name,
        ),
    )

    assert result.succeeded
    assert result.dispatch_count == 1
    assert result.dispatches[0].mode == "chain"
    assert result.dispatches[0].operation_indexes == [0, 1]
    new_pane_id = result.created_panes["child"]
    assert new_pane_id.startswith("%")

    asyncio.run(
        wait_for_channel(channel, timeout=5.0, socket_name=mcp_server.socket_name)
    )
    mcp_pane.window.refresh()
    new_pane = mcp_pane.window.panes.get(pane_id=new_pane_id)
    assert new_pane is not None
    assert "CC_OPS_REF" in "\n".join(new_pane.capture_pane())


def test_run_tmux_operations_continue_uses_standalone_dispatches(
    mcp_session: Session,
) -> None:
    """Continue mode preserves later operations instead of native chain abort."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_operations(
            operations=[
                TmuxSendKeysOperation(pane_id="%999999", keys="bad", enter=False),
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
    assert result.dispatch_count == 2
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.FAILED,
        TmuxOperationStatus.SUCCEEDED,
    ]
    assert server.cmd("show-option", "-gv", "@cc_ops_after_error").stdout == ["set"]


class CompileContractCase(t.NamedTuple):
    """Case for libtmux compiler contract failures."""

    test_id: str
    contract: t.Literal["chainable", "scope"]
    expected_error: str


@pytest.mark.parametrize(
    "case",
    [
        CompileContractCase(
            test_id="chainability_contract",
            contract="chainable",
            expected_error="not chainable from test",
        ),
        CompileContractCase(
            test_id="scope_contract",
            contract="scope",
            expected_error="wrong scope from test",
        ),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_surfaces_libtmux_contract_errors(
    case: CompileContractCase,
    mcp_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compiler reports libtmux contract drift as an operation failure.

    The contract metadata is static, so this uses monkeypatch instead of a
    tmux fixture to simulate libtmux rejecting a command.
    """
    if case.contract == "chainable":

        def fail_chainable(command_name: str) -> None:
            msg = f"{command_name} {case.expected_error}"
            raise ChainabilityError(msg)

        monkeypatch.setattr(
            chain_tools,
            "ensure_chainable",
            fail_chainable,
            raising=False,
        )
    else:

        def fail_scope(command_name: str, target_scope: str) -> None:
            msg = f"{command_name} {target_scope} {case.expected_error}"
            raise CommandScopeError(msg)

        monkeypatch.setattr(
            chain_tools,
            "validate_command_scope",
            fail_scope,
            raising=False,
        )

    result = asyncio.run(
        run_tmux_operations(
            operations=[
                SetOptionOperation(
                    option="@cc_ops_contract_error",
                    value="set",
                    global_=True,
                ),
            ],
            socket_name=mcp_session.server.socket_name,
        ),
    )

    assert not result.succeeded
    assert result.dispatch_count == 0
    assert result.steps[0].status == TmuxOperationStatus.FAILED
    assert result.steps[0].stderr is not None
    assert case.expected_error in result.steps[0].stderr[0]


class DryRunSetOptionCase(t.NamedTuple):
    """Case for dry-run option chains."""

    test_id: str
    operations: list[TmuxOperation]
    absent_options: list[str]


@pytest.mark.parametrize(
    "case",
    [
        DryRunSetOptionCase(
            test_id="folded_global_options",
            operations=[
                SetOptionOperation(option="@cc_ops_dry_a", value="1", global_=True),
                SetOptionOperation(option="@cc_ops_dry_b", value="2", global_=True),
            ],
            absent_options=["@cc_ops_dry_a", "@cc_ops_dry_b"],
        ),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_dry_run_plans_without_mutating(
    case: DryRunSetOptionCase,
    mcp_session: Session,
) -> None:
    """Dry-run returns planned dispatches without changing tmux state."""
    server = mcp_session.server
    result = asyncio.run(
        run_tmux_operations(
            operations=case.operations,
            dry_run=True,
            socket_name=server.socket_name,
        ),
    )

    assert result.succeeded
    assert result.dry_run
    assert result.dispatch_count == 1
    assert result.dispatches[0].mode == "chain"
    assert result.dispatches[0].returncode is None
    assert ";" in result.dispatches[0].argv
    assert [step.status for step in result.steps] == [
        TmuxOperationStatus.PLANNED,
        TmuxOperationStatus.PLANNED,
    ]
    assert all(step.returncode is None for step in result.steps)
    for option in case.absent_options:
        assert server.cmd("show-option", "-gv", option).stdout == []


class DryRunSplitRefCase(t.NamedTuple):
    """Case for dry-run split refs."""

    test_id: str
    ref: str
    keys: str


@pytest.mark.parametrize(
    "case",
    [
        DryRunSplitRefCase(
            test_id="marked_split_ref",
            ref="child",
            keys="printf 'DRY_RUN_REF\\n'",
        ),
    ],
    ids=lambda case: case.test_id,
)
def test_run_tmux_operations_dry_run_plans_marked_split_ref(
    case: DryRunSplitRefCase,
    mcp_server: Server,
    mcp_pane: Pane,
) -> None:
    """Dry-run uses placeholders for pane refs without creating panes."""
    mcp_pane.window.refresh()
    pane_count = len(mcp_pane.window.panes)

    result = asyncio.run(
        run_tmux_operations(
            operations=[
                SplitPaneOperation(ref=case.ref, pane_id=mcp_pane.pane_id),
                TmuxSendKeysOperation(pane_ref=case.ref, keys=case.keys),
            ],
            dry_run=True,
            socket_name=mcp_server.socket_name,
        ),
    )

    placeholder = f"<pane_ref:{case.ref}>"
    assert result.succeeded
    assert result.dry_run
    assert result.dispatch_count == 1
    assert result.dispatches[0].mode == "chain"
    assert result.dispatches[0].returncode is None
    assert result.dispatches[0].operation_indexes == [0, 1]
    assert result.created_panes == {case.ref: placeholder}
    assert result.steps[0].status == TmuxOperationStatus.PLANNED
    assert result.steps[0].created_pane_id == placeholder
    assert result.steps[1].status == TmuxOperationStatus.PLANNED

    mcp_pane.window.refresh()
    assert len(mcp_pane.window.panes) == pane_count


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
            run_tmux_operations(
                operations=t.cast("list[TmuxOperation]", case.operations),
                socket_name=mcp_session.server.socket_name,
            ),
        )
