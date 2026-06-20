"""Typed MCP tool for compiling tmux operations into native dispatches."""

from __future__ import annotations

import asyncio
import typing as t

from libtmux._experimental.chain import (
    ChainabilityError,
    CommandCall,
    CommandChain,
    CommandResultLike,
    CommandRunner,
    CommandScope,
    CommandScopeError,
    ensure_chainable,
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

_PendingCalls: t.TypeAlias = tuple[int, str, tuple[CommandCall, ...]]
_MarkedDecorate: t.TypeAlias = tuple[int, TmuxOperation]


class _CompileError(Exception):
    """Operation-level compile failure that should become a step result."""


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


def _ensure_chainable_calls(calls: tuple[CommandCall, ...]) -> None:
    """Raise a compile error unless every call may fold into a tmux chain."""
    try:
        for call in calls:
            ensure_chainable(call.name)
    except ChainabilityError as exc:
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


def _is_output_operation(operation: TmuxOperation) -> bool:
    """Return whether an operation must run outside a pending chain."""
    return isinstance(operation, CapturePaneOperation) or (
        isinstance(operation, SplitPaneOperation) and operation.ref is not None
    )


def _collect_marked_decorates(
    operations: list[TmuxOperation],
    start: int,
    pane_ref: str,
) -> tuple[list[_MarkedDecorate], int]:
    """Collect immediate operations that can target a fresh split via {marked}."""
    decorates: list[_MarkedDecorate] = []
    index = start + 1
    while index < len(operations):
        operation = operations[index]
        if (
            isinstance(operation, (TmuxSendKeysOperation, ResizePaneOperation))
            and operation.pane_id is None
            and operation.pane_ref == pane_ref
        ):
            decorates.append((index, operation))
            index += 1
            continue
        break
    return decorates, index


def _marked_split_calls(
    operation: SplitPaneOperation,
    split_calls: tuple[CommandCall, ...],
    decorates: list[_MarkedDecorate],
    created_panes: dict[str, str],
) -> tuple[CommandCall, ...]:
    """Build the folded command calls for a ref-producing split."""
    if operation.ref is None:
        msg = "marked split dispatch requires a split ref"
        raise _CompileError(msg)

    marked_created = {**created_panes, operation.ref: "{marked}"}
    calls = [*split_calls, CommandCall("select-pane", ("-m",))]
    for _, decorate in decorates:
        calls.extend(_operation_calls(decorate, marked_created))
    calls.append(CommandCall("select-pane", ("-M",)))
    marked_calls = tuple(calls)
    _ensure_chainable_calls(marked_calls)
    return marked_calls


def _run_calls(
    runner: CommandRunner,
    calls: tuple[CommandCall, ...],
) -> tuple[list[str], CommandResultLike]:
    """Run one operation's calls as a single native dispatch."""
    if len(calls) == 1:
        argv = _calls_argv(calls)
        result = runner.cmd(argv[0], *argv[1:])
        return argv, result

    chain = CommandChain(calls)
    result = chain.run(runner)
    return list(chain.argv()), result


def _calls_argv(calls: tuple[CommandCall, ...]) -> list[str]:
    """Render calls as one native tmux dispatch argv."""
    if len(calls) == 1:
        return list(calls[0].argv())
    return list(CommandChain(calls).argv())


def _dispatch_standalone(
    runner: CommandRunner,
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


def _dispatch_marked_split(
    runner: CommandRunner,
    index: int,
    operation: SplitPaneOperation,
    calls: tuple[CommandCall, ...],
    decorates: list[_MarkedDecorate],
) -> tuple[TmuxOperationDispatchResult, list[TmuxOperationStepResult], str | None]:
    """Run one id-producing split and its immediate decorates via {marked}."""
    chain = CommandChain(calls)
    result = chain.run(runner)
    stdout = list(result.stdout)
    stderr = list(result.stderr)
    created_pane_id: str | None = None
    status = TmuxOperationStatus.SUCCEEDED
    if result.returncode != 0:
        status = TmuxOperationStatus.FAILED
    elif stdout:
        created_pane_id = stdout[0]
    else:
        status = TmuxOperationStatus.FAILED
        stderr = [*stderr, "split-pane did not return a pane id"]

    dispatch = TmuxOperationDispatchResult(
        mode="chain",
        operation_indexes=[index, *(decorate_index for decorate_index, _ in decorates)],
        argv=list(chain.argv()),
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    steps = [
        TmuxOperationStepResult(
            index=index,
            kind=operation.kind,
            status=status,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
            created_pane_id=created_pane_id,
        ),
        *[
            TmuxOperationStepResult(
                index=decorate_index,
                kind=decorate.kind,
                status=status,
                returncode=result.returncode,
                stdout=stdout if status == TmuxOperationStatus.FAILED else None,
                stderr=stderr if status == TmuxOperationStatus.FAILED else None,
            )
            for decorate_index, decorate in decorates
        ],
    ]
    return dispatch, steps, created_pane_id


def _dispatch_chain(
    runner: CommandRunner,
    pending: list[_PendingCalls],
) -> tuple[TmuxOperationDispatchResult, list[TmuxOperationStepResult]]:
    """Run pending operations as one tmux command sequence."""
    calls = tuple(call for _, _, op_calls in pending for call in op_calls)
    chain = CommandChain(calls)
    result = chain.run(runner)
    stdout = list(result.stdout)
    stderr = list(result.stderr)
    status = (
        TmuxOperationStatus.SUCCEEDED
        if result.returncode == 0
        else TmuxOperationStatus.FAILED
    )
    dispatch = TmuxOperationDispatchResult(
        mode="chain",
        operation_indexes=[index for index, _, _ in pending],
        argv=list(chain.argv()),
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    steps = [
        TmuxOperationStepResult(
            index=index,
            kind=kind,
            status=status,
            returncode=result.returncode,
            stdout=stdout if status == TmuxOperationStatus.FAILED else None,
            stderr=stderr if status == TmuxOperationStatus.FAILED else None,
        )
        for index, kind, _ in pending
    ]
    return dispatch, steps


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
    """Return the dry-run shape for one standalone dispatch."""
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


def _plan_marked_split(
    index: int,
    operation: SplitPaneOperation,
    calls: tuple[CommandCall, ...],
    decorates: list[_MarkedDecorate],
) -> tuple[TmuxOperationDispatchResult, list[TmuxOperationStepResult], str | None]:
    """Return the dry-run shape for one folded split-ref dispatch."""
    created_pane_id = _planned_pane_ref(operation.ref) if operation.ref else None
    return (
        TmuxOperationDispatchResult(
            mode="chain",
            operation_indexes=[
                index,
                *(decorate_index for decorate_index, _ in decorates),
            ],
            argv=list(CommandChain(calls).argv()),
            returncode=None,
        ),
        [
            _planned_step(index, operation.kind, created_pane_id),
            *[
                _planned_step(decorate_index, decorate.kind)
                for decorate_index, decorate in decorates
            ],
        ],
        created_pane_id,
    )


def _plan_chain(
    pending: list[_PendingCalls],
) -> tuple[TmuxOperationDispatchResult, list[TmuxOperationStepResult]]:
    """Return the dry-run shape for a pending folded chain."""
    calls = tuple(call for _, _, op_calls in pending for call in op_calls)
    dispatch = TmuxOperationDispatchResult(
        mode="chain",
        operation_indexes=[index for index, _, _ in pending],
        argv=list(CommandChain(calls).argv()),
        returncode=None,
    )
    return dispatch, [_planned_step(index, kind) for index, kind, _ in pending]


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
    """Return timeout results for one standalone dispatch."""
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


def _timeout_marked_split(
    index: int,
    operation: SplitPaneOperation,
    calls: tuple[CommandCall, ...],
    decorates: list[_MarkedDecorate],
    dispatch_timeout: float,
) -> tuple[TmuxOperationDispatchResult, list[TmuxOperationStepResult], str | None]:
    """Return timeout results for one folded split-ref dispatch."""
    stderr = _timeout_stderr(dispatch_timeout)
    return (
        TmuxOperationDispatchResult(
            mode="chain",
            operation_indexes=[
                index,
                *(decorate_index for decorate_index, _ in decorates),
            ],
            argv=list(CommandChain(calls).argv()),
            returncode=None,
            stderr=stderr,
        ),
        [
            _timeout_step(index, operation.kind, stderr),
            *[
                _timeout_step(decorate_index, decorate.kind, stderr)
                for decorate_index, decorate in decorates
            ],
        ],
        None,
    )


def _timeout_chain(
    pending: list[_PendingCalls],
    dispatch_timeout: float,
) -> tuple[TmuxOperationDispatchResult, list[TmuxOperationStepResult]]:
    """Return timeout results for a pending folded chain."""
    stderr = _timeout_stderr(dispatch_timeout)
    calls = tuple(call for _, _, op_calls in pending for call in op_calls)
    dispatch = TmuxOperationDispatchResult(
        mode="chain",
        operation_indexes=[index for index, _, _ in pending],
        argv=list(CommandChain(calls).argv()),
        returncode=None,
        stderr=stderr,
    )
    return (
        dispatch,
        [_timeout_step(index, kind, stderr) for index, kind, _ in pending],
    )


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


def _steps_succeeded(
    steps: t.Iterable[TmuxOperationStepResult],
    *,
    dry_run: bool,
) -> bool:
    """Return whether every step succeeded for control-flow purposes."""
    return all(_step_succeeded(step, dry_run=dry_run) for step in steps)


@handle_tool_errors_async
async def run_tmux_operations(
    operations: list[TmuxOperation],
    on_error: t.Literal["stop", "continue"] = "stop",
    dry_run: bool = False,
    dispatch_timeout: float | None = 10.0,
    socket_name: str | None = None,
) -> RunTmuxOperationsResult:
    """Run typed tmux operations with minimum safe native dispatches.

    Consecutive chainable, no-output operations fold into one tmux
    ``a ; b ; c`` sequence. Output operations such as ``capture_pane`` run as
    standalone dispatches so their stdout can be attributed to the correct
    operation. A single id-producing ``split_pane`` may still fold with
    immediate decorations that target its ref through tmux's ``{marked}``
    register.
    ``on_error="continue"`` disables folding because tmux sequences abort the
    rest of the sequence on first failure.
    ``dispatch_timeout`` bounds how long the tool waits for one native tmux
    dispatch; timed-out subprocess work may still finish in the background.
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

    runner = None if dry_run else _get_server(socket_name=socket_name)
    pending: list[_PendingCalls] = []
    dispatches: list[TmuxOperationDispatchResult] = []
    steps_by_index: dict[int, TmuxOperationStepResult] = {}
    created_panes: dict[str, str] = {}

    async def flush_pending() -> bool:
        if not pending:
            return True
        if dry_run:
            dispatch, steps = _plan_chain(pending)
        else:
            assert runner is not None
            pending_snapshot = list(pending)
            try:
                chain_dispatch_coro = asyncio.to_thread(
                    _dispatch_chain,
                    runner,
                    pending_snapshot,
                )
                if dispatch_timeout is None:
                    dispatch, steps = await chain_dispatch_coro
                else:
                    dispatch, steps = await asyncio.wait_for(
                        chain_dispatch_coro,
                        timeout=dispatch_timeout,
                    )
            except TimeoutError:
                assert dispatch_timeout is not None
                dispatch, steps = _timeout_chain(pending_snapshot, dispatch_timeout)
        dispatches.append(dispatch)
        pending.clear()
        for step in steps:
            steps_by_index[step.index] = step
        return _steps_succeeded(steps, dry_run=dry_run)

    index = 0
    while index < len(validated):
        operation = validated[index]
        try:
            calls = _operation_calls(operation, created_panes)
        except _CompileError as exc:
            if not await flush_pending():
                for skip_index, skipped in enumerate(validated[index:], start=index):
                    steps_by_index[skip_index] = _skipped_step(skip_index, skipped)
                break
            steps_by_index[index] = _compile_failure_step(index, operation, exc)
            if on_error == "stop":
                for skip_index, skipped in enumerate(
                    validated[index + 1 :],
                    start=index + 1,
                ):
                    steps_by_index[skip_index] = _skipped_step(skip_index, skipped)
                break
            index += 1
            continue

        if (
            on_error == "stop"
            and isinstance(operation, SplitPaneOperation)
            and operation.ref is not None
        ):
            decorates, next_index = _collect_marked_decorates(
                validated,
                index,
                operation.ref,
            )
            if decorates:
                if not await flush_pending():
                    for skip_index, skipped in enumerate(
                        validated[index:], start=index
                    ):
                        steps_by_index[skip_index] = _skipped_step(skip_index, skipped)
                    break
                try:
                    marked_calls = _marked_split_calls(
                        operation,
                        calls,
                        decorates,
                        created_panes,
                    )
                except _CompileError as exc:
                    steps_by_index[index] = _compile_failure_step(
                        index,
                        operation,
                        exc,
                    )
                    for skip_index, skipped in enumerate(
                        validated[index + 1 :],
                        start=index + 1,
                    ):
                        steps_by_index[skip_index] = _skipped_step(
                            skip_index,
                            skipped,
                        )
                    break
                if dry_run:
                    dispatch, steps, created_pane_id = _plan_marked_split(
                        index,
                        operation,
                        marked_calls,
                        decorates,
                    )
                else:
                    assert runner is not None
                    decorates_snapshot = list(decorates)
                    try:
                        marked_dispatch_coro = asyncio.to_thread(
                            _dispatch_marked_split,
                            runner,
                            index,
                            operation,
                            marked_calls,
                            decorates_snapshot,
                        )
                        if dispatch_timeout is None:
                            (
                                dispatch,
                                steps,
                                created_pane_id,
                            ) = await marked_dispatch_coro
                        else:
                            dispatch, steps, created_pane_id = await asyncio.wait_for(
                                marked_dispatch_coro,
                                timeout=dispatch_timeout,
                            )
                    except TimeoutError:
                        assert dispatch_timeout is not None
                        dispatch, steps, created_pane_id = _timeout_marked_split(
                            index,
                            operation,
                            marked_calls,
                            decorates_snapshot,
                            dispatch_timeout,
                        )
                dispatches.append(dispatch)
                for step in steps:
                    steps_by_index[step.index] = step
                if created_pane_id is not None:
                    created_panes[operation.ref] = created_pane_id
                if not _steps_succeeded(steps, dry_run=dry_run):
                    for skip_index, skipped in enumerate(
                        validated[next_index:],
                        start=next_index,
                    ):
                        steps_by_index[skip_index] = _skipped_step(
                            skip_index,
                            skipped,
                        )
                    break
                index = next_index
                continue

        force_standalone = on_error == "continue" or _is_output_operation(operation)
        if not force_standalone:
            try:
                _ensure_chainable_calls(calls)
            except _CompileError as exc:
                if not await flush_pending():
                    for skip_index, skipped in enumerate(
                        validated[index:],
                        start=index,
                    ):
                        steps_by_index[skip_index] = _skipped_step(
                            skip_index,
                            skipped,
                        )
                    break
                steps_by_index[index] = _compile_failure_step(index, operation, exc)
                if on_error == "stop":
                    for skip_index, skipped in enumerate(
                        validated[index + 1 :],
                        start=index + 1,
                    ):
                        steps_by_index[skip_index] = _skipped_step(
                            skip_index,
                            skipped,
                        )
                    break
                index += 1
                continue
            pending.append((index, operation.kind, calls))
            index += 1
            continue

        if not await flush_pending() and on_error == "stop":
            for skip_index, skipped in enumerate(validated[index:], start=index):
                steps_by_index[skip_index] = _skipped_step(skip_index, skipped)
            break

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
                created_pane_id=planned_pane_id if capture_created_pane else None,
            )
        else:
            assert runner is not None
            try:
                standalone_dispatch_coro = asyncio.to_thread(
                    _dispatch_standalone,
                    runner,
                    index,
                    operation.kind,
                    calls,
                    capture_created_pane=capture_created_pane,
                )
                if dispatch_timeout is None:
                    dispatch, step, created_pane_id = await standalone_dispatch_coro
                else:
                    dispatch, step, created_pane_id = await asyncio.wait_for(
                        standalone_dispatch_coro,
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
        if (
            isinstance(operation, SplitPaneOperation)
            and operation.ref is not None
            and created_pane_id is not None
        ):
            created_panes[operation.ref] = created_pane_id
        if not _step_succeeded(step, dry_run=dry_run) and on_error == "stop":
            for skip_index, skipped in enumerate(
                validated[index + 1 :],
                start=index + 1,
            ):
                steps_by_index[skip_index] = _skipped_step(skip_index, skipped)
            break
        index += 1

    if pending:
        await flush_pending()

    steps = [steps_by_index[index] for index in range(len(validated))]
    succeeded = _steps_succeeded(steps, dry_run=dry_run)
    return RunTmuxOperationsResult(
        succeeded=succeeded,
        dry_run=dry_run,
        dispatch_count=len(dispatches),
        dispatches=dispatches,
        steps=steps,
        created_panes=created_panes,
    )


def register(mcp: FastMCP) -> None:
    """Register typed chain tools with the MCP instance."""
    mcp.tool(
        title="Run tmux Operations",
        annotations=ANNOTATIONS_SHELL,
        tags={TAG_MUTATING},
    )(run_tmux_operations)
