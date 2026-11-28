"""The GUI's executor: each request runs as a `QRunnable` on one of four pools (architecture §3.1 "GUI").

The GUI thread never calls `Backend.execute`. A pool thread runs the request with the call's
cancel flag and a progress sink; the sink and the completion emit signals of a relay object
that lives on the GUI thread, through queued connections, so callbacks (and the widgets they
touch) only run on the GUI thread, in the order the events were emitted, and never inside the
call that caused them. The bridge passes requests, events and responses on unchanged.
"""

import logging
import threading
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from typing import override

import shiboken6
from PySide6.QtCore import QDeadlineTimer, QObject, QRunnable, Qt, QThreadPool, Signal

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, ProgressEvent, Request, Response
from transcribe_ui.api.constants import ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE
from transcribe_ui.gui.constants import ACTION_POOLS, CALL_DONE, DEFAULT_POOL, POOL_THREADS, SHORT_POOL, PoolName
from transcribe_ui.gui.executor import (
    CallHandle,
    DoneCallback,
    PooledCall,
    ProgressCallback,
    RequestRunner,
    StartedCallback,
)
from transcribe_ui.placement.classes import ImportCustomPlanRequest

logger = logging.getLogger(__name__)

# Sees every request and its response on the GUI thread before the caller does (the writer-lock notice).
type ResponseObserver = Callable[[Request, Response], None]


def pool_of(action: str) -> PoolName:
    """Return the pool a request action runs on."""
    return ACTION_POOLS.get(action, DEFAULT_POOL)


def pool_for(request: Request) -> PoolName:
    """Return the pool a request runs on: its action's, except a custom plan only checked (short, it writes nothing)."""
    if isinstance(request, ImportCustomPlanRequest) and request.check_only:
        return SHORT_POOL
    return pool_of(request.action)


class _Relay(QObject):
    """Lives on the GUI thread; pool threads emit, the GUI thread receives (queued)."""

    started = Signal(object)
    progressed = Signal(object, object)
    finished = Signal(object, object)


@dataclass(frozen=True)
class _Callbacks:
    """What the sender of one call wants to hear about it."""

    on_done: DoneCallback
    on_progress: ProgressCallback | None
    on_started: StartedCallback | None


class _RelaySink:
    """Progress sink of one call: hands each event to the GUI thread."""

    def __init__(self, relay: _Relay, call: PooledCall) -> None:
        self._relay = relay
        self._call = call

    def emit(self, event: ProgressEvent) -> None:
        self._relay.progressed.emit(self._call, event)


def _internal_error(request: Request) -> ErrorResponse:
    body = ErrorBody(code=ERROR_INTERNAL, message=INTERNAL_ERROR_MESSAGE)
    return ErrorResponse(action=request.action, error=body)


class _CallRunnable(QRunnable):
    """Runs one call on a pool thread.

    The pool deletes it after `run` (auto-delete, the default), and `clear()` deletes it while
    still queued, so finished calls do not pile up. `take_off` is used only while its call is
    queued; a runnable taken off is handed back by Qt and deleted here.

    PySide's `QThreadPool.start` releases the GIL to start the runnable and only then records
    the pool as its parent; a runnable that ran and was auto-deleted in between would keep that
    parent reference (and its call) for good. So `run` waits for `handed_over`, which `submit`
    sets once `start` has returned: the deletion always comes after the parent was recorded,
    and removes it.
    """

    def __init__(self, backend: RequestRunner, relay: _Relay, pool: QThreadPool) -> None:
        super().__init__()
        self._backend = backend
        self._relay = relay
        self._pool = pool
        self.call: PooledCall | None = None
        self.handed_over = threading.Event()

    def take_off(self) -> bool:
        """Remove the runnable from its queue if it has not started; return whether it was."""
        if not self._pool.tryTake(self):
            return False
        # The pool gave ownership back; nothing else deletes it now.
        shiboken6.delete(self)
        return True

    @override
    def run(self) -> None:
        self.handed_over.wait()
        call = self.call
        if call is None or not call.begin():
            return
        self._relay.started.emit(call)
        sink = _RelaySink(self._relay, call)
        try:
            response = self._backend.execute(call.request, sink, call.flag)
        except Exception:
            # `Backend.execute` never raises; this keeps a faulty runner from leaving a call open forever.
            logger.exception("Request %s failed outside the backend", call.request.action)
            response = _internal_error(call.request)
        call.finish(response)


class _Dispatcher:
    """GUI-thread side: hands each call's events to its sender, tracks the calls not yet delivered."""

    def __init__(self) -> None:
        self.live: dict[PooledCall, _Callbacks] = {}
        self.observers: list[ResponseObserver] = []
        # Set by `QtExecutor.close`: events are dropped, no callback runs.
        self.closed = False
        self._idle_listeners: list[Callable[[], None]] = []

    def when_idle(self, listener: Callable[[], None]) -> None:
        if self.live:
            self._idle_listeners.append(listener)
        else:
            listener()

    def started(self, call: PooledCall) -> None:
        callbacks = None if self.closed else self.live.get(call)
        if callbacks is not None and callbacks.on_started is not None:
            callbacks.on_started()

    def progressed(self, call: PooledCall, event: ProgressEvent) -> None:
        callbacks = None if self.closed else self.live.get(call)
        if callbacks is not None and callbacks.on_progress is not None:
            callbacks.on_progress(event)

    def finished(self, call: PooledCall, response: Response) -> None:
        callbacks = self.live.pop(call, None)
        call.mark_delivered()
        if callbacks is None or self.closed:
            return
        with ExitStack() as after:
            # Even when a callback fails (its exception reaches the log), the close dialog must hear that all is done.
            after.callback(self._notify_idle)
            for observer in self.observers:
                observer(call.request, response)
            callbacks.on_done(response)

    def _notify_idle(self) -> None:
        if self.live:
            return
        listeners = self._idle_listeners
        self._idle_listeners = []
        for listener in listeners:
            listener()


def _start(pool: QThreadPool, runnable: _CallRunnable, call: PooledCall, live: dict[PooledCall, _Callbacks]) -> None:
    """Queue the runnable; if that fails, forget its call (nobody will answer it) and re-raise."""
    try:
        pool.start(runnable)
    except BaseException:
        # Never queued: it must not keep the window from closing.
        live.pop(call, None)
        raise
    finally:
        # Whatever happened, a pool thread that got the runnable must not wait for ever.
        runnable.handed_over.set()


class QtExecutor:
    """Sends requests on the `scan`, `long`, `short` and `poll` pools; create and use it on the GUI thread."""

    def __init__(self, backend: RequestRunner, pool_threads: Mapping[PoolName, int] = POOL_THREADS) -> None:
        """Run requests on this backend with pools of these sizes."""
        self._backend = backend
        self._dispatcher = _Dispatcher()
        self._relay = _Relay()
        queued = Qt.ConnectionType.QueuedConnection
        self._relay.started.connect(self._dispatcher.started, queued)
        self._relay.progressed.connect(self._dispatcher.progressed, queued)
        self._relay.finished.connect(self._dispatcher.finished, queued)
        self._pools: dict[PoolName, QThreadPool] = {}
        for name, threads in pool_threads.items():
            pool = QThreadPool()
            pool.setMaxThreadCount(threads)
            self._pools[name] = pool
        self._shut = False

    def observe(self, observer: ResponseObserver) -> None:
        """Show every request and its response to `observer` before its sender (observers in the order added)."""
        self._dispatcher.observers.append(observer)

    def submit(
        self,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None = None,
        on_started: StartedCallback | None = None,
    ) -> CallHandle:
        """Queue the request on its pool; after `shutdown` it completes as `cancelled` without running."""
        pool = self._pools[pool_for(request)]
        runnable = _CallRunnable(self._backend, self._relay, pool)
        call = PooledCall(request, runnable.take_off, self._deliver)
        runnable.call = call
        self._dispatcher.live[call] = _Callbacks(on_done, on_progress, on_started)
        if self._shut:
            call.abandon()
        else:
            _start(pool, runnable, call, self._dispatcher.live)
        return call

    def pending(self) -> int:
        """Return how many calls have not delivered their response yet."""
        return len(self._dispatcher.live)

    def running(self) -> int:
        """Return how many calls are queued or running."""
        return sum(1 for call in self._dispatcher.live if call.state != CALL_DONE)

    def when_idle(self, listener: Callable[[], None]) -> None:
        """Call `listener` once, on the GUI thread, when no call is pending (at once if none is)."""
        self._dispatcher.when_idle(listener)

    def shutdown(self) -> None:
        """Close: queued calls never start (they complete as `cancelled`), running ones get their flag set.

        Later sends complete as `cancelled` without running.
        """
        self._shut = True
        for pool in self._pools.values():
            pool.clear()
        for call in list(self._dispatcher.live):
            call.abandon()

    def close(self) -> None:
        """Run no callback any more (the window is gone): answers still on their way are dropped.

        Safe while calls still run: their threads keep emitting, and the GUI thread drops what
        arrives instead of reaching views that were deleted.
        """
        self._shut = True
        self._dispatcher.closed = True

    def wait_for_threads(self, timeout_ms: int = -1) -> bool:
        """Wait until every pool thread is idle, at most `timeout_ms` in all (-1: forever)."""
        if timeout_ms < 0:
            deadline = QDeadlineTimer(QDeadlineTimer.ForeverConstant.Forever)
        else:
            deadline = QDeadlineTimer(timeout_ms)
        # One deadline shared by every pool, so the pools cannot each wait the full timeout in turn.
        return all(pool.waitForDone(deadline) for pool in self._pools.values())

    def _deliver(self, call: PooledCall, response: Response) -> None:
        # Any thread; always queued, so the sender hears it later on the GUI thread.
        self._relay.finished.emit(call, response)
