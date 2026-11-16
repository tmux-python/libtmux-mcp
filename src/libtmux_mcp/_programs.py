"""Keep program owners reachable across MCP request cancellation."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import threading
import typing as t
import uuid

import anyio
from libtmux import Program, ProgramExit, ProgramScreen

from libtmux_mcp._utils import ExpectedToolError
from libtmux_mcp.models import ProgramObservation

if t.TYPE_CHECKING:
    from libtmux import ProgramRecovery, Server

_T = t.TypeVar("_T")
_MAX_RUNS = 32


def _error_text(error: object, limit: int = 2048) -> str:
    """Bound error evidence in UTF-8 bytes without splitting a code point."""
    return str(error).encode("utf-8")[:limit].decode("utf-8", "ignore")


async def _run_in_worker(call: t.Callable[[threading.Event], _T]) -> _T:
    """Cancel native work cooperatively, then join it before leaving the handler."""
    cancel = threading.Event()
    # Reservations bound concurrency; this limiter avoids unrelated worker queues.
    task = asyncio.create_task(
        anyio.to_thread.run_sync(call, cancel, limiter=anyio.CapacityLimiter(1))
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        with anyio.CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:  # noqa: BLE001 - the worker records its retained owner.
                    break
        with contextlib.suppress(BaseException):
            task.result()
        raise


@dataclasses.dataclass
class _Run:
    run_id: str
    sequence: int
    operation: t.Literal["run", "capabilities"]
    session_id: str | None
    socket_name: str | None
    socket_path: str | None
    status: t.Literal["running", "cleanup_required", "cleanup_in_progress"] = "running"
    owner: Program | None = None
    recovery: ProgramRecovery | None = None
    error: str | None = None
    cleanup_errors: tuple[str, ...] = ()
    last_observation: ProgramObservation | None = None


class _ProgramRunError(ExpectedToolError):
    """Carry a cleanup record through the existing tool-error middleware."""

    def __init__(
        self,
        message: str,
        recovery_id: str | None = None,
        cleanup_errors: tuple[str, ...] = (),
        observation: ProgramObservation | None = None,
    ) -> None:
        self.program_recovery_id = recovery_id
        self.program_cleanup_errors = cleanup_errors
        self.program_observation = observation
        super().__init__(
            _error_text(message),
            suggestion=(
                "Inspect list_program_runs and retry "
                f"cleanup_program(run_id={recovery_id!r})."
                if recovery_id is not None
                else None
            ),
        )


class _ProgramRuns:
    """Reserve capacity before launch; remove records only after verified cleanup."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, _Run] = {}
        self._sequence = 0

    def reserve(
        self,
        operation: t.Literal["run", "capabilities"],
        session_id: str | None,
        server: Server,
    ) -> _Run:
        with self._lock:
            if len(self._runs) >= _MAX_RUNS:
                msg = (
                    "Program capacity is full; inspect list_program_runs "
                    "and clean up retained runs"
                )
                raise ExpectedToolError(msg)
            self._sequence += 1
            run = _Run(
                uuid.uuid4().hex,
                self._sequence,
                operation,
                session_id,
                server.socket_name,
                str(server.socket_path) if server.socket_path is not None else None,
            )
            self._runs[run.run_id] = run
            return run

    def retire(self, run: _Run) -> None:
        with self._lock:
            del self._runs[run.run_id]

    def failed(self, run: _Run, error: BaseException) -> _ProgramRunError:
        owner = getattr(error, "owner", None)
        observed = getattr(error, "last_observation", None)
        capture = getattr(error, "last_capture", None)
        raw = (
            capture.text.encode("utf-8") if isinstance(capture, ProgramScreen) else None
        )
        observation = (
            ProgramObservation(
                exit_status=observed.exit_status
                if isinstance(observed, ProgramExit)
                else None,
                exit_signal=observed.exit_signal
                if isinstance(observed, ProgramExit)
                else None,
                screen_text=raw[-2048:].decode("utf-8", "ignore")
                if raw is not None
                else None,
                screen_truncated=raw is not None and len(raw) > 2048,
            )
            if isinstance(observed, ProgramExit) or raw is not None
            else None
        )
        failures = tuple(
            _error_text(item) for item in getattr(error, "cleanup_errors", ())
        )[-8:]
        if isinstance(owner, Program) and owner.recovery.cleanup_pending:
            try:
                owner.cleanup(timeout=0.5)
            except BaseException as cleanup_error:  # noqa: BLE001 - retain partial-start owners too.
                failures = (*failures, _error_text(cleanup_error))[-8:]
        if isinstance(owner, Program) and owner.recovery.cleanup_pending:
            with self._lock:
                run.owner = owner
                run.recovery = owner.recovery
                run.error = _error_text(error)
                run.cleanup_errors = failures
                run.last_observation = observation
                run.status = "cleanup_required"
            return _ProgramRunError(str(error), run.run_id, failures, observation)
        self.retire(run)
        return _ProgramRunError(
            str(error), cleanup_errors=failures, observation=observation
        )

    async def execute(
        self,
        call: t.Callable[[threading.Event], _T],
        operation: t.Literal["run", "capabilities"],
        server: Server,
        session_id: str | None = None,
    ) -> _T:
        """Bound queued workers as well as running and unresolved owners."""
        run = self.reserve(operation, session_id, server)
        entered = threading.Event()

        def work(cancel: threading.Event) -> _T:
            entered.set()
            try:
                result = call(cancel)
            except BaseException as error:
                raise self.failed(run, error) from error
            self.retire(run)
            return result

        try:
            return await _run_in_worker(work)
        finally:
            if not entered.is_set():
                self.retire(run)

    @staticmethod
    def _snapshot(run: _Run, *, details: bool = False) -> dict[str, t.Any]:
        data: dict[str, t.Any] = {
            "run_id": run.run_id,
            "operation": run.operation,
            "session_id": run.session_id,
            "socket_name": run.socket_name,
            "socket_path": run.socket_path,
            "status": run.status,
            "pane_id": run.recovery.pane_id if run.recovery is not None else None,
            "pane_state": run.recovery.pane_state if run.recovery is not None else None,
            "cleanup_pending": run.recovery.cleanup_pending
            if run.recovery is not None
            else None,
        }
        if details:
            data.update(
                error=run.error,
                cleanup_errors=list(run.cleanup_errors),
                last_observation=run.last_observation,
            )
        else:
            data["error_preview"] = (
                _error_text(run.error, 256) if run.error is not None else None
            )
        return data

    def page(self, cursor: str | None, limit: int) -> dict[str, t.Any]:
        after = int(cursor) if cursor is not None else 0
        with self._lock:
            following = [run for run in self._runs.values() if run.sequence > after]
            selected = following[:limit]
            return {
                "items": [self._snapshot(run) for run in selected],
                "next_cursor": str(selected[-1].sequence)
                if len(following) > limit
                else None,
            }

    def inspect(self, run_id: str) -> dict[str, t.Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                msg = "No retained program has that run_id"
                raise ExpectedToolError(msg)
            return self._snapshot(run, details=True)

    async def cleanup(self, run_id: str) -> str | None:
        """Claim the retained owner before scheduling its one cleanup worker."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                msg = "No retained program has that run_id"
                raise ExpectedToolError(msg)
            if run.status != "cleanup_required" or run.owner is None:
                msg = "The worker is still settling; inspect list_program_runs again"
                raise ExpectedToolError(msg)
            run.status = "cleanup_in_progress"
            owner = run.owner
        entered = threading.Event()

        def work(cancel: threading.Event) -> str | None:
            entered.set()
            try:
                if cancel.is_set():
                    msg = "Program cleanup was cancelled before admission"
                    raise _ProgramRunError(msg, run_id)  # noqa: TRY301 - retain retry state.
                if owner.recovery.pane_state == "receipt-recorded":
                    owner.terminate(timeout=0.5)
                owner.cleanup(timeout=0.5)
                if owner.recovery.cleanup_pending:
                    msg = "Program cleanup remains unverified"
                    raise _ProgramRunError(msg, run_id)  # noqa: TRY301 - retain retry state.
            except BaseException as error:
                with self._lock:
                    run.error = _error_text(error)
                    run.cleanup_errors = (
                        *run.cleanup_errors,
                        *(
                            _error_text(item)
                            for item in getattr(error, "cleanup_errors", ())
                        ),
                        _error_text(error),
                    )[-8:]
                    run.recovery = owner.recovery
                    run.status = "cleanup_required"
                raise _ProgramRunError(
                    str(error), run_id, run.cleanup_errors, run.last_observation
                ) from error
            self.retire(run)
            return owner.recovery.pane_id

        try:
            return await _run_in_worker(work)
        finally:
            if not entered.is_set():
                with self._lock:
                    run.status = "cleanup_required"


_program_runs = _ProgramRuns()
