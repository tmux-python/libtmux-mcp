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
    TAG_MUTATING,
    ExpectedToolError,
    _get_server,
    handle_tool_errors_async,
)
from libtmux_mcp.models import (
    CapturePaneOperation,
    ResizePaneOperation,
    RunTmuxOperationsResult,
    SelectLayoutOperation,
    SetOptionOperation,
    SplitPaneOperation,
    TmuxOperation,
    TmuxOperationDispatchResult,
    TmuxOperationStatus,
    TmuxOperationStepResult,
    TmuxSendKeysOperation,
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


def _operation_scope(operation: TmuxOperation) -> CommandScope:
    """Return the tmux target scope for one typed operation."""
    if isinstance(
        operation,
        (
            SplitPaneOperation,
            TmuxSendKeysOperation,
            ResizePaneOperation,
            CapturePaneOperation,
        ),
    ):
        return "pane"
    if isinstance(operation, SelectLayoutOperation):
        return "window"
    if isinstance(operation, SetOptionOperation):
        scope: CommandScope
        scope = operation.scope if operation.scope is not None else "server"
        return scope
    assert_never(operation)


def _validate_operation_scope(
    operation: TmuxOperation,
    calls: tuple[CommandCall, ...],
) -> None:
    """Validate typed operation targets against libtmux command metadata."""
    scope = _operation_scope(operation)
    try:
        for call in calls:
            validate_command_scope(call.name, scope)
    except CommandScopeError as exc:
        raise _CompileError(str(exc)) from exc


def _target_pane(
    pane_id: str | None,
    pane_ref: str | None,
    created_panes: dict[str, str],
) -> str:
    """Return the concrete pane target for an operation."""
    if pane_id is not None:
        return pane_id
    if pane_ref is None:
        msg = "operation is missing pane_id or pane_ref"
        raise _CompileError(msg)
    try:
        return created_panes[pane_ref]
    except KeyError as exc:
        msg = f"unknown pane_ref: {pane_ref}"
        raise _CompileError(msg) from exc


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
            target=_target_pane(operation.pane_id, operation.pane_ref, created_panes),
        ),
    )


def _send_keys_calls(
    operation: TmuxSendKeysOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build one operation's ``send-keys`` calls."""
    target = _target_pane(operation.pane_id, operation.pane_ref, created_panes)
    if operation.literal:
        calls = [
            CommandCall("send-keys", ("-l", operation.keys), target=target),
        ]
        if operation.enter:
            calls.append(CommandCall("send-keys", ("Enter",), target=target))
        return tuple(calls)

    args: list[str] = [operation.keys]
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
            target=_target_pane(operation.pane_id, operation.pane_ref, created_panes),
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
            target=_target_pane(operation.pane_id, operation.pane_ref, created_panes),
        ),
    )


def _operation_calls(
    operation: TmuxOperation,
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Lower one typed operation to tmux command calls."""
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
) -> tuple[TmuxOperationDispatchResult, TmuxOperationStepResult, str | None]:
    """Run one operation and return dispatch, step, and captured pane id."""
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
            mode="standalone",
            operation_indexes=[index],
            argv=argv,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
        ),
        TmuxOperationStepResult(
            index=index,
            kind=kind,
            status=status,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
            created_pane_id=created_pane_id,
        ),
        created_pane_id,
    )


def _planned_pane_ref(ref: str) -> str:
    """Return the deterministic placeholder for a dry-run pane ref."""
    return f"<pane_ref:{ref}>"


def _planned_step(
    index: int,
    kind: str,
    created_pane_id: str | None = None,
) -> TmuxOperationStepResult:
    """Return a planned step result for dry-run compilation."""
    return TmuxOperationStepResult(
        index=index,
        kind=kind,
        status=TmuxOperationStatus.PLANNED,
        created_pane_id=created_pane_id,
    )


def _plan_standalone(
    index: int,
    kind: str,
    calls: tuple[CommandCall, ...],
    *,
    created_pane_id: str | None = None,
) -> tuple[TmuxOperationDispatchResult, TmuxOperationStepResult, str | None]:
    """Return the dry-run shape for one operation dispatch."""
    return (
        TmuxOperationDispatchResult(
            mode="standalone",
            operation_indexes=[index],
            argv=_calls_argv(calls),
            returncode=None,
        ),
        _planned_step(index, kind, created_pane_id),
        created_pane_id,
    )


def _timeout_stderr(dispatch_timeout: float) -> list[str]:
    """Return the stderr payload for a bounded dispatch timeout."""
    return [f"tmux dispatch timed out after {dispatch_timeout:g} seconds"]


def _timeout_step(
    index: int,
    kind: str,
    stderr: list[str],
) -> TmuxOperationStepResult:
    """Return a failed step for a dispatch timeout."""
    return TmuxOperationStepResult(
        index=index,
        kind=kind,
        status=TmuxOperationStatus.FAILED,
        stderr=stderr,
    )


def _timeout_standalone(
    index: int,
    kind: str,
    calls: tuple[CommandCall, ...],
    dispatch_timeout: float,
) -> tuple[TmuxOperationDispatchResult, TmuxOperationStepResult, str | None]:
    """Return timeout results for one operation dispatch."""
    stderr = _timeout_stderr(dispatch_timeout)
    return (
        TmuxOperationDispatchResult(
            mode="standalone",
            operation_indexes=[index],
            argv=_calls_argv(calls),
            returncode=None,
            stderr=stderr,
        ),
        _timeout_step(index, kind, stderr),
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


def _compile_failure_step(
    index: int,
    operation: TmuxOperation,
    error: Exception,
) -> TmuxOperationStepResult:
    """Convert a compile failure into a step result."""
    return TmuxOperationStepResult(
        index=index,
        kind=operation.kind,
        status=TmuxOperationStatus.FAILED,
        stderr=[str(error)],
    )


def _skipped_step(index: int, operation: TmuxOperation) -> TmuxOperationStepResult:
    """Return a skipped result for an operation after stop-on-error."""
    return TmuxOperationStepResult(
        index=index,
        kind=operation.kind,
        status=TmuxOperationStatus.SKIPPED,
    )


def _step_succeeded(step: TmuxOperationStepResult, *, dry_run: bool) -> bool:
    """Return whether a step should allow later operations to continue."""
    return step.status == TmuxOperationStatus.SUCCEEDED or (
        dry_run and step.status == TmuxOperationStatus.PLANNED
    )


@handle_tool_errors_async
async def run_tmux_operations(
    operations: list[TmuxOperation],
    on_error: t.Literal["stop", "continue"] = "stop",
    dry_run: bool = False,
    dispatch_timeout: float | None = 10.0,
    rollback_on_error: bool = False,
    socket_name: str | None = None,
) -> RunTmuxOperationsResult:
    """Run typed tmux operations, one dispatch per operation.

    Each operation is dispatched on its own over a persistent ``tmux -C``
    control connection, so every operation keeps its own stdout and return
    code. ``on_error="stop"`` (the default) stops before the next operation
    once one fails or its target cannot be resolved, marking the rest as
    skipped; ``on_error="continue"`` records each failure and runs the rest.
    ``dry_run`` returns the rendered dispatch plan without touching tmux.
    ``dispatch_timeout`` bounds how long the tool waits for one native tmux
    dispatch; timed-out work may still finish in the background.
    ``rollback_on_error`` kills panes created by ref-producing ``split_pane``
    operations when the overall operation list fails.
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
        steps_by_index: dict[int, TmuxOperationStepResult] = {}
        created_panes: dict[str, str] = {}
        created_pane_order: list[str] = []

        def record_created_pane(ref: str, pane_id: str) -> None:
            created_panes[ref] = pane_id
            if pane_id not in created_pane_order:
                created_pane_order.append(pane_id)

        def skip_rest(start: int) -> None:
            for skip_index, skipped in enumerate(validated[start:], start=start):
                steps_by_index[skip_index] = _skipped_step(skip_index, skipped)

        index = 0
        while index < len(validated):
            operation = validated[index]
            try:
                calls = _operation_calls(operation, created_panes)
            except _CompileError as exc:
                steps_by_index[index] = _compile_failure_step(index, operation, exc)
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
                dispatch, step, created_pane_id = _plan_standalone(
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
                        dispatch, step, created_pane_id = await dispatch_coro
                    else:
                        dispatch, step, created_pane_id = await asyncio.wait_for(
                            dispatch_coro,
                            timeout=dispatch_timeout,
                        )
                except TimeoutError:
                    assert dispatch_timeout is not None
                    dispatch, step, created_pane_id = _timeout_standalone(
                        index,
                        operation.kind,
                        calls,
                        dispatch_timeout,
                    )
            dispatches.append(dispatch)
            steps_by_index[index] = step
            if capture_created_pane and created_pane_id is not None:
                assert isinstance(operation, SplitPaneOperation)
                assert operation.ref is not None
                record_created_pane(operation.ref, created_pane_id)
            if not _step_succeeded(step, dry_run=dry_run) and on_error == "stop":
                skip_rest(index + 1)
                break
            index += 1

        steps = [steps_by_index[index] for index in range(len(validated))]
        succeeded = all(_step_succeeded(step, dry_run=dry_run) for step in steps)
        rolled_back_panes: list[str] = []
        rollback_errors: list[str] = []
        if rollback_on_error and not dry_run and not succeeded and created_pane_order:
            assert runner is not None
            rolled_back_panes, rollback_errors = await asyncio.to_thread(
                _rollback_created_panes,
                runner,
                created_pane_order,
            )
        return RunTmuxOperationsResult(
            succeeded=succeeded,
            dry_run=dry_run,
            dispatch_count=len(dispatches),
            dispatches=dispatches,
            steps=steps,
            created_panes=created_panes,
            rolled_back_panes=rolled_back_panes,
            rollback_errors=rollback_errors,
        )
    finally:
        if runner is not None:
            await asyncio.to_thread(runner.close)


def register(mcp: FastMCP) -> None:
    """Register typed chain tools with the MCP instance."""
    mcp.tool(
        title="Run tmux Operations",
        annotations=ANNOTATIONS_SHELL,
        tags={TAG_MUTATING},
    )(run_tmux_operations)
