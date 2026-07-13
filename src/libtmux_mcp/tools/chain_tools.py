"""Typed MCP tool for running tmux operations over a control connection."""

from __future__ import annotations

import asyncio
import dataclasses
import typing as t

from libtmux._experimental.chain import (
    CommandCall,
    CommandChain,
    CommandResultLike,
    CommandScope,
    CommandScopeError,
    ControlModeRunner,
    validate_command_scope,
)
from pydantic import TypeAdapter

from libtmux_mcp._utils import (
    ANNOTATIONS_SHELL,
    TAG_DESTRUCTIVE,
    TAG_MUTATING,
    ExpectedToolError,
    _get_server,
    effective_safety_level,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    CapturePaneOperation,
    CapturePaneStepResult,
    KillPaneOperation,
    MakeGridOperation,
    OperationStepResult,
    PaneIdTarget,
    PaneTarget,
    RefTarget,
    ResizePaneOperation,
    RunTmuxDiagnostics,
    RunTmuxPlanResult,
    SelectLayoutOperation,
    SetOptionOperation,
    SplitEvenlyOperation,
    SplitPaneOperation,
    SplitPaneStepResult,
    TmuxOperation,
    TmuxOperationDispatchResult,
    TmuxOperationStatus,
    TmuxSendKeysOperation,
    TmuxStepResult,
)

if t.TYPE_CHECKING:
    from fastmcp import FastMCP
    from typing_extensions import assert_never
else:

    def assert_never(value: object) -> t.NoReturn:
        """Runtime fallback for the type-checker-only exhaustiveness helper."""
        msg = f"unhandled operation: {value!r}"
        raise AssertionError(msg)


TMUX_OPERATIONS_ADAPTER: TypeAdapter[list[TmuxOperation]] = TypeAdapter(
    list[TmuxOperation],
)


class _CompileError(Exception):
    """Operation-level compile failure that should become a step result."""


@dataclasses.dataclass
class _Outcome:
    """Internal per-operation outcome before shaping into a typed result."""

    index: int
    kind: str
    status: TmuxOperationStatus
    stdout: list[str] = dataclasses.field(default_factory=list)
    stderr: list[str] = dataclasses.field(default_factory=list)
    created_pane_id: str | None = None


@dataclasses.dataclass
class _CombinedResult:
    """A ``CommandResultLike`` merging several control-mode command results."""

    stdout: list[str]
    stderr: list[str]
    returncode: int


def _combine_results(
    results: t.Sequence[CommandResultLike],
) -> _CombinedResult:
    """Merge per-command results; the first non-zero return code wins."""
    stdout = [line for result in results for line in result.stdout]
    stderr = [line for result in results for line in result.stderr]
    returncode = next(
        (result.returncode for result in results if result.returncode != 0),
        0,
    )
    return _CombinedResult(stdout=stdout, stderr=stderr, returncode=returncode)


_FIXED_COMMAND_SCOPE: dict[str, CommandScope] = {
    "split-window": "pane",
    "send-keys": "pane",
    "resize-pane": "pane",
    "capture-pane": "pane",
    "select-layout": "window",
    "kill-pane": "pane",
}


def _call_scope(operation: TmuxOperation, call: CommandCall) -> CommandScope:
    """Return the tmux target scope for one command of an operation."""
    if isinstance(operation, SetOptionOperation):
        return operation.scope if operation.scope is not None else "server"
    return _FIXED_COMMAND_SCOPE[call.name]


def _validate_operation_scope(
    operation: TmuxOperation,
    calls: tuple[CommandCall, ...],
) -> None:
    """Validate each command's target scope against libtmux command metadata."""
    try:
        for call in calls:
            validate_command_scope(call.name, _call_scope(operation, call))
    except CommandScopeError as exc:
        raise _CompileError(str(exc)) from exc


def _resolve_target(
    target: PaneTarget,
    created_panes: dict[str, str],
) -> str:
    """Resolve a typed pane target to a concrete tmux target token."""
    if isinstance(target, PaneIdTarget):
        return target.pane_id
    if isinstance(target, RefTarget):
        try:
            return created_panes[target.ref]
        except KeyError as exc:
            msg = f"unknown ref: {target.ref}"
            raise _CompileError(msg) from exc
    assert_never(target)


def _split_calls(
    operation: SplitPaneOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build ``split-window`` calls for a typed split operation."""
    args: list[str] = []
    if operation.horizontal:
        args.append("-h")
    if operation.ref is not None:
        args.extend(("-P", "-F", "#{pane_id}"))
    # Pin the new pane to the target pane's directory. Without ``-c`` tmux
    # resolves the cwd from the control client's context rather than the target
    # pane, so an explicit format keeps splits deterministic.
    args.extend(("-c", "#{pane_current_path}"))
    if operation.shell is not None:
        args.append(operation.shell)
    return (
        CommandCall(
            "split-window",
            tuple(args),
            target=_resolve_target(operation.target, created_panes),
        ),
    )


def _send_keys_calls(
    operation: TmuxSendKeysOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build one operation's ``send-keys`` calls."""
    target = _resolve_target(operation.target, created_panes)
    keys = (" " if operation.suppress_history else "") + operation.keys
    if operation.literal:
        calls = [
            CommandCall("send-keys", ("-l", keys), target=target),
        ]
        if operation.enter:
            calls.append(CommandCall("send-keys", ("Enter",), target=target))
        return tuple(calls)

    args: list[str] = [keys]
    if operation.enter:
        args.append("Enter")
    return (CommandCall("send-keys", tuple(args), target=target),)


def _resize_pane_calls(
    operation: ResizePaneOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build ``resize-pane`` calls for a typed resize operation."""
    args: list[str | int] = []
    if operation.zoom:
        args.append("-Z")
    if operation.height is not None:
        args.extend(("-y", operation.height))
    if operation.width is not None:
        args.extend(("-x", operation.width))
    return (
        CommandCall(
            "resize-pane",
            tuple(args),
            target=_resolve_target(operation.target, created_panes),
        ),
    )


def _select_layout_calls(operation: SelectLayoutOperation) -> tuple[CommandCall, ...]:
    """Build ``select-layout`` calls for a typed layout operation."""
    return (
        CommandCall("select-layout", (operation.layout,), target=operation.window_id),
    )


def _set_option_calls(operation: SetOptionOperation) -> tuple[CommandCall, ...]:
    """Build ``set-option`` calls for a typed option operation."""
    args: list[str] = []
    if operation.global_:
        args.append("-g")
    if operation.scope == "server":
        args.append("-s")
    elif operation.scope == "window":
        args.append("-w")
    elif operation.scope == "pane":
        args.append("-p")
    args.extend((operation.option, operation.value))
    return (CommandCall("set-option", tuple(args), target=operation.target),)


def _capture_pane_calls(
    operation: CapturePaneOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build ``capture-pane`` calls for a typed capture operation."""
    args: list[str | int] = ["-p"]
    if operation.start is not None:
        args.extend(("-S", operation.start))
    if operation.end is not None:
        args.extend(("-E", operation.end))
    return (
        CommandCall(
            "capture-pane",
            tuple(args),
            target=_resolve_target(operation.target, created_panes),
        ),
    )


def _split_evenly_calls(
    operation: SplitEvenlyOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build splits plus an even layout for a typed split-evenly operation."""
    target = _resolve_target(operation.target, created_panes)
    flag = "-h" if operation.axis == "horizontal" else "-v"
    layout = "even-horizontal" if operation.axis == "horizontal" else "even-vertical"
    calls = [
        CommandCall(
            "split-window",
            (flag, "-c", "#{pane_current_path}"),
            target=target,
        )
        for _ in range(operation.count - 1)
    ]
    calls.append(CommandCall("select-layout", (layout,), target=target))
    return tuple(calls)


def _make_grid_calls(
    operation: MakeGridOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build splits plus a tiled layout for a typed make-grid operation."""
    target = _resolve_target(operation.target, created_panes)
    panes = operation.rows * operation.cols
    calls = [
        CommandCall(
            "split-window",
            ("-c", "#{pane_current_path}"),
            target=target,
        )
        for _ in range(panes - 1)
    ]
    calls.append(CommandCall("select-layout", ("tiled",), target=target))
    return tuple(calls)


def _kill_pane_calls(
    operation: KillPaneOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build ``kill-pane`` calls for a typed kill operation."""
    return (
        CommandCall(
            "kill-pane",
            (),
            target=_resolve_target(operation.target, created_panes),
        ),
    )


def _operation_calls(
    operation: TmuxOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Lower one typed operation to tmux command calls."""
    if (
        isinstance(operation, KillPaneOperation)
        and effective_safety_level() != TAG_DESTRUCTIVE
    ):
        msg = "kill_pane requires the destructive safety tier"
        raise _CompileError(msg)
    if isinstance(operation, SplitPaneOperation):
        calls = _split_calls(operation, created_panes)
    elif isinstance(operation, TmuxSendKeysOperation):
        calls = _send_keys_calls(operation, created_panes)
    elif isinstance(operation, ResizePaneOperation):
        calls = _resize_pane_calls(operation, created_panes)
    elif isinstance(operation, SelectLayoutOperation):
        calls = _select_layout_calls(operation)
    elif isinstance(operation, SetOptionOperation):
        calls = _set_option_calls(operation)
    elif isinstance(operation, CapturePaneOperation):
        calls = _capture_pane_calls(operation, created_panes)
    elif isinstance(operation, SplitEvenlyOperation):
        calls = _split_evenly_calls(operation, created_panes)
    elif isinstance(operation, MakeGridOperation):
        calls = _make_grid_calls(operation, created_panes)
    elif isinstance(operation, KillPaneOperation):
        calls = _kill_pane_calls(operation, created_panes)
    else:
        assert_never(operation)
    _validate_operation_scope(operation, calls)
    return calls


def _calls_argv(calls: tuple[CommandCall, ...]) -> list[str]:
    """Render an operation's calls for the dispatch record."""
    if len(calls) == 1:
        return list(calls[0].argv())
    return list(CommandChain(calls).argv())


def _run_calls(
    runner: ControlModeRunner,
    calls: tuple[CommandCall, ...],
) -> tuple[list[str], CommandResultLike]:
    """Run one operation's calls over the control connection."""
    results = runner.run_calls(calls)
    return _calls_argv(calls), _combine_results(results)


def _dispatch_standalone(
    runner: ControlModeRunner,
    index: int,
    kind: str,
    calls: tuple[CommandCall, ...],
    *,
    capture_created_pane: bool,
) -> tuple[TmuxOperationDispatchResult, _Outcome, str | None]:
    """Run one operation and return dispatch, outcome, and captured pane id."""
    argv, result = _run_calls(runner, calls)
    stdout = list(result.stdout)
    stderr = list(result.stderr)
    created_pane_id: str | None = None
    status = TmuxOperationStatus.SUCCEEDED
    if result.returncode != 0:
        status = TmuxOperationStatus.FAILED
    elif capture_created_pane:
        if stdout:
            created_pane_id = stdout[0]
        else:
            status = TmuxOperationStatus.FAILED
            stderr = [*stderr, "split-pane did not return a pane id"]

    return (
        TmuxOperationDispatchResult(
            index=index,
            argv=argv,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
        ),
        _Outcome(
            index=index,
            kind=kind,
            status=status,
            stdout=stdout,
            stderr=stderr,
            created_pane_id=created_pane_id,
        ),
        created_pane_id,
    )


def _planned_pane_ref(ref: str) -> str:
    """Return the deterministic placeholder for a dry-run pane ref."""
    return f"<pane_ref:{ref}>"


def _plan_standalone(
    index: int,
    kind: str,
    calls: tuple[CommandCall, ...],
    *,
    created_pane_id: str | None = None,
) -> tuple[TmuxOperationDispatchResult, _Outcome, str | None]:
    """Return the dry-run shape for one operation dispatch."""
    return (
        TmuxOperationDispatchResult(
            index=index,
            argv=_calls_argv(calls),
            returncode=None,
        ),
        _Outcome(
            index=index,
            kind=kind,
            status=TmuxOperationStatus.PLANNED,
            created_pane_id=created_pane_id,
        ),
        created_pane_id,
    )


def _timeout_stderr(dispatch_timeout: float) -> list[str]:
    """Return the stderr payload for a bounded dispatch timeout."""
    return [f"tmux dispatch timed out after {dispatch_timeout:g} seconds"]


def _timeout_standalone(
    index: int,
    kind: str,
    calls: tuple[CommandCall, ...],
    dispatch_timeout: float,
) -> tuple[TmuxOperationDispatchResult, _Outcome, str | None]:
    """Return timeout results for one operation dispatch."""
    stderr = _timeout_stderr(dispatch_timeout)
    return (
        TmuxOperationDispatchResult(
            index=index,
            argv=_calls_argv(calls),
            returncode=None,
            stderr=stderr,
        ),
        _Outcome(
            index=index,
            kind=kind,
            status=TmuxOperationStatus.FAILED,
            stderr=stderr,
        ),
        None,
    )


def _rollback_created_panes(
    runner: ControlModeRunner,
    pane_ids: list[str],
) -> tuple[list[str], list[str]]:
    """Kill created panes in reverse order and report cleanup failures."""
    rolled_back_panes: list[str] = []
    rollback_errors: list[str] = []
    for pane_id in reversed(pane_ids):
        result = runner.cmd("kill-pane", "-t", pane_id)
        if result.returncode == 0:
            rolled_back_panes.append(pane_id)
            continue
        stderr = list(result.stderr) or [f"kill-pane exited {result.returncode}"]
        rollback_errors.extend(f"{pane_id}: {line}" for line in stderr)
    return rolled_back_panes, rollback_errors


def _compile_failure_outcome(
    index: int,
    operation: TmuxOperation,
    error: Exception,
) -> _Outcome:
    """Convert a compile failure into an outcome."""
    return _Outcome(
        index=index,
        kind=operation.kind,
        status=TmuxOperationStatus.FAILED,
        stderr=[str(error)],
    )


def _skipped_outcome(index: int, operation: TmuxOperation) -> _Outcome:
    """Return a skipped outcome for an operation after stop-on-error."""
    return _Outcome(
        index=index,
        kind=operation.kind,
        status=TmuxOperationStatus.SKIPPED,
    )


def _outcome_succeeded(outcome: _Outcome, *, dry_run: bool) -> bool:
    """Return whether an outcome should allow later operations to continue."""
    return outcome.status == TmuxOperationStatus.SUCCEEDED or (
        dry_run and outcome.status == TmuxOperationStatus.PLANNED
    )


def _to_step_result(outcome: _Outcome) -> TmuxStepResult:
    """Shape an internal outcome into the typed, per-kind step result."""
    error = "\n".join(outcome.stderr) if outcome.stderr else None
    if outcome.kind == "split_pane":
        return SplitPaneStepResult(
            index=outcome.index,
            status=outcome.status,
            pane_id=outcome.created_pane_id,
            error=error,
        )
    if outcome.kind == "capture_pane":
        lines = (
            outcome.stdout if outcome.status == TmuxOperationStatus.SUCCEEDED else None
        )
        return CapturePaneStepResult(
            index=outcome.index,
            status=outcome.status,
            lines=lines,
            error=error,
        )
    status_kind = t.cast(
        "t.Literal['send_keys', 'resize_pane', 'select_layout', 'set_option', "
        "'split_evenly', 'make_grid', 'kill_pane']",
        outcome.kind,
    )
    return OperationStepResult(
        kind=status_kind,
        index=outcome.index,
        status=outcome.status,
        error=error,
    )


@handle_tool_errors_async
async def run_tmux_plan(
    operations: list[TmuxOperation],
    on_error: t.Literal["stop", "continue"] = "stop",
    dry_run: bool = False,
    dispatch_timeout: float | None = 10.0,
    rollback_on_error: bool = False,
    explain: bool = False,
    socket_name: str | None = None,
) -> RunTmuxPlanResult:
    """Run typed tmux operations, one dispatch per operation.

    Each operation is dispatched on its own over a persistent ``tmux -C``
    control connection, so every operation keeps its own stdout and return
    code. The result carries one typed, per-kind ``steps`` entry per
    operation: ``capture_pane`` returns ``lines``, ``split_pane`` returns
    ``pane_id``, and the rest return status only.

    ``on_error="stop"`` (the default) stops before the next operation once one
    fails or its target cannot be resolved, marking the rest as skipped;
    ``on_error="continue"`` records each failure and runs the rest.
    ``dry_run`` returns the planned steps without touching tmux.
    ``dispatch_timeout`` bounds how long the tool waits for one native tmux
    dispatch; timed-out work may still finish in the background.
    ``rollback_on_error`` kills panes created by ref-producing ``split_pane``
    operations when the overall operation list fails.
    ``explain`` attaches per-dispatch diagnostics (rendered argv and raw
    stdout/stderr) under ``diagnostics``.
    """
    validated = TMUX_OPERATIONS_ADAPTER.validate_python(operations)
    if not validated:
        msg = "operations must not be empty"
        raise ExpectedToolError(msg)
    if on_error not in {"stop", "continue"}:
        msg = "on_error must be 'stop' or 'continue'"
        raise ExpectedToolError(msg)
    if dispatch_timeout is not None and dispatch_timeout <= 0:
        msg = "dispatch_timeout must be greater than 0 or null"
        raise ExpectedToolError(msg)

    runner: ControlModeRunner | None = None
    if not dry_run:
        runner = ControlModeRunner(_get_server(socket_name=socket_name))
    try:
        dispatches: list[TmuxOperationDispatchResult] = []
        outcomes_by_index: dict[int, _Outcome] = {}
        created_panes: dict[str, str] = {}
        created_pane_order: list[str] = []

        def record_created_pane(ref: str, pane_id: str) -> None:
            created_panes[ref] = pane_id
            if pane_id not in created_pane_order:
                created_pane_order.append(pane_id)

        def skip_rest(start: int) -> None:
            for skip_index, skipped in enumerate(validated[start:], start=start):
                outcomes_by_index[skip_index] = _skipped_outcome(skip_index, skipped)

        index = 0
        while index < len(validated):
            operation = validated[index]
            try:
                calls = _operation_calls(operation, created_panes)
            except _CompileError as exc:
                outcomes_by_index[index] = _compile_failure_outcome(
                    index, operation, exc
                )
                if on_error == "stop":
                    skip_rest(index + 1)
                    break
                index += 1
                continue

            capture_created_pane = (
                isinstance(operation, SplitPaneOperation) and operation.ref is not None
            )
            if dry_run:
                planned_pane_id = (
                    _planned_pane_ref(operation.ref)
                    if isinstance(operation, SplitPaneOperation)
                    and operation.ref is not None
                    else None
                )
                dispatch, outcome, created_pane_id = _plan_standalone(
                    index,
                    operation.kind,
                    calls,
                    created_pane_id=planned_pane_id,
                )
            else:
                assert runner is not None
                try:
                    dispatch_coro = asyncio.to_thread(
                        _dispatch_standalone,
                        runner,
                        index,
                        operation.kind,
                        calls,
                        capture_created_pane=capture_created_pane,
                    )
                    if dispatch_timeout is None:
                        dispatch, outcome, created_pane_id = await dispatch_coro
                    else:
                        dispatch, outcome, created_pane_id = await asyncio.wait_for(
                            dispatch_coro,
                            timeout=dispatch_timeout,
                        )
                except TimeoutError:
                    assert dispatch_timeout is not None
                    dispatch, outcome, created_pane_id = _timeout_standalone(
                        index,
                        operation.kind,
                        calls,
                        dispatch_timeout,
                    )
            dispatches.append(dispatch)
            outcomes_by_index[index] = outcome
            if capture_created_pane and created_pane_id is not None:
                assert isinstance(operation, SplitPaneOperation)
                assert operation.ref is not None
                record_created_pane(operation.ref, created_pane_id)
            if not _outcome_succeeded(outcome, dry_run=dry_run) and on_error == "stop":
                skip_rest(index + 1)
                break
            index += 1

        outcomes = [outcomes_by_index[index] for index in range(len(validated))]
        succeeded = all(
            _outcome_succeeded(outcome, dry_run=dry_run) for outcome in outcomes
        )
        rolled_back_panes: list[str] = []
        rollback_errors: list[str] = []
        if rollback_on_error and not dry_run and not succeeded and created_pane_order:
            assert runner is not None
            rolled_back_panes, rollback_errors = await asyncio.to_thread(
                _rollback_created_panes,
                runner,
                created_pane_order,
            )
        diagnostics = (
            RunTmuxDiagnostics(dispatch_count=len(dispatches), dispatches=dispatches)
            if explain
            else None
        )
        return RunTmuxPlanResult(
            succeeded=succeeded,
            dry_run=dry_run,
            steps=[_to_step_result(outcome) for outcome in outcomes],
            created_panes=created_panes,
            rolled_back_panes=rolled_back_panes,
            rollback_errors=rollback_errors,
            diagnostics=diagnostics,
        )
    finally:
        if runner is not None:
            await asyncio.to_thread(runner.close)


def register(mcp: FastMCP) -> None:
    """Register typed chain tools with the MCP instance."""
    mcp.tool(
        title="Run tmux Plan",
        annotations=ANNOTATIONS_SHELL,
        tags={TAG_MUTATING},
    )(run_tmux_plan)
