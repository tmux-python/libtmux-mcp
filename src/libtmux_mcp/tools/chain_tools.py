"""Typed MCP tool for compiling tmux operations into native dispatches."""

from __future__ import annotations

import asyncio
import typing as t

from libtmux._experimental.chain import (
    CommandCall,
    CommandChain,
    CommandResultLike,
    CommandRunner,
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

TMUX_OPERATIONS_ADAPTER: TypeAdapter[list[TmuxOperation]] = TypeAdapter(
    list[TmuxOperation],
)

_PendingCalls: t.TypeAlias = tuple[int, str, tuple[CommandCall, ...]]


class _CompileError(Exception):
    """Operation-level compile failure that should become a step result."""


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
        return _split_calls(operation, created_panes)
    if isinstance(operation, TmuxSendKeysOperation):
        return _send_keys_calls(operation, created_panes)
    if isinstance(operation, ResizePaneOperation):
        return _resize_pane_calls(operation, created_panes)
    if isinstance(operation, SelectLayoutOperation):
        return _select_layout_calls(operation)
    if isinstance(operation, SetOptionOperation):
        return _set_option_calls(operation)
    if isinstance(operation, CapturePaneOperation):
        return _capture_pane_calls(operation, created_panes)
    msg = f"unsupported operation type: {type(operation).__name__}"
    raise TypeError(msg)


def _is_output_operation(operation: TmuxOperation) -> bool:
    """Return whether an operation must run outside a pending chain."""
    return isinstance(operation, CapturePaneOperation) or (
        isinstance(operation, SplitPaneOperation) and operation.ref is not None
    )


def _run_calls(
    runner: CommandRunner,
    calls: tuple[CommandCall, ...],
) -> tuple[list[str], CommandResultLike]:
    """Run one operation's calls as a single native dispatch."""
    if len(calls) == 1:
        argv = list(calls[0].argv())
        result = runner.cmd(argv[0], *argv[1:])
        return argv, result

    chain = CommandChain(calls)
    result = chain.run(runner)
    return list(chain.argv()), result


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


@handle_tool_errors_async
async def run_tmux_operations(
    operations: list[TmuxOperation],
    on_error: t.Literal["stop", "continue"] = "stop",
    socket_name: str | None = None,
) -> RunTmuxOperationsResult:
    """Run typed tmux operations with minimum safe native dispatches.

    Consecutive chainable, no-output operations fold into one tmux
    ``a ; b ; c`` sequence. Operations that need per-step output, such as
    ``capture_pane`` and id-producing ``split_pane`` refs, run as standalone
    dispatches so their stdout can be attributed to the correct operation.
    ``on_error="continue"`` disables folding because tmux sequences abort the
    rest of the sequence on first failure.
    """
    validated = TMUX_OPERATIONS_ADAPTER.validate_python(operations)
    if not validated:
        msg = "operations must not be empty"
        raise ExpectedToolError(msg)
    if on_error not in {"stop", "continue"}:
        msg = "on_error must be 'stop' or 'continue'"
        raise ExpectedToolError(msg)

    runner = _get_server(socket_name=socket_name)
    pending: list[_PendingCalls] = []
    dispatches: list[TmuxOperationDispatchResult] = []
    steps_by_index: dict[int, TmuxOperationStepResult] = {}
    created_panes: dict[str, str] = {}

    async def flush_pending() -> bool:
        if not pending:
            return True
        dispatch, steps = await asyncio.to_thread(_dispatch_chain, runner, pending)
        dispatches.append(dispatch)
        pending.clear()
        for step in steps:
            steps_by_index[step.index] = step
        return all(step.status == TmuxOperationStatus.SUCCEEDED for step in steps)

    for index, operation in enumerate(validated):
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
            continue

        force_standalone = on_error == "continue" or _is_output_operation(operation)
        if not force_standalone:
            pending.append((index, operation.kind, calls))
            continue

        if not await flush_pending() and on_error == "stop":
            for skip_index, skipped in enumerate(validated[index:], start=index):
                steps_by_index[skip_index] = _skipped_step(skip_index, skipped)
            break

        capture_created_pane = (
            isinstance(operation, SplitPaneOperation) and operation.ref is not None
        )
        dispatch, step, created_pane_id = await asyncio.to_thread(
            _dispatch_standalone,
            runner,
            index,
            operation.kind,
            calls,
            capture_created_pane=capture_created_pane,
        )
        dispatches.append(dispatch)
        steps_by_index[index] = step
        if (
            isinstance(operation, SplitPaneOperation)
            and operation.ref is not None
            and created_pane_id is not None
        ):
            created_panes[operation.ref] = created_pane_id
        if step.status != TmuxOperationStatus.SUCCEEDED and on_error == "stop":
            for skip_index, skipped in enumerate(
                validated[index + 1 :],
                start=index + 1,
            ):
                steps_by_index[skip_index] = _skipped_step(skip_index, skipped)
            break

    if pending:
        await flush_pending()

    steps = [steps_by_index[index] for index in range(len(validated))]
    succeeded = all(step.status == TmuxOperationStatus.SUCCEEDED for step in steps)
    return RunTmuxOperationsResult(
        succeeded=succeeded,
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
