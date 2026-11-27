"""The `Executor` port of the view-models, call handles and channels (architecture §3.1 "GUI").

Qt-free: view-models send requests through an `Executor` and never touch Qt, so they are
tested on `SyncExecutor`. The GUI's executor (`bridge.QtExecutor`) runs each request on a
thread pool and delivers progress events and the response on the GUI thread, unchanged and in
order. Channels say what may run at once: `ExclusiveChannel` (one call per key),
`LatestChannel` (a new send replaces the previous call of its key) and `SingleFlightChannel`
(the poll: no new tick while one is queued or running).
"""

import threading
from collections.abc import Callable, Hashable
from functools import partial
from typing import Protocol

from transcribe_ui.api.classes import ErrorResponse, ProgressEvent, Request, Response
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag, CancelToken, ProgressSink
from transcribe_ui.gui.constants import CALL_DONE, CALL_QUEUED, CALL_RUNNING, POLL_KEY, CallState

# Receives the response of a call (on the GUI thread for `QtExecutor`).
type DoneCallback = Callable[[Response], None]
# Receives one progress event of a call (on the GUI thread for `QtExecutor`).
type ProgressCallback = Callable[[ProgressEvent], None]
# Told that a call left its queue and runs now (on the GUI thread for `QtExecutor`).
type StartedCallback = Callable[[], None]


class RequestRunner(Protocol):
    """What executes a request: `Backend` (tests: fakes that record or gate calls)."""

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Carry out one request; failures come back as `ErrorResponse`."""
        ...


class CallHandle(Protocol):
    """A sent request: where it is, and a way to cancel it."""

    @property
    def state(self) -> CallState:
        """Return whether the call is queued, running or done."""
        ...

    def cancel(self) -> None:
        """Cancel the call: a queued one never runs, a running one stops between steps."""
        ...


class Executor(Protocol):
    """Sends requests to the backend for the view-models; callbacks run on the GUI thread."""

    def submit(
        self,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None = None,
        on_started: StartedCallback | None = None,
    ) -> CallHandle:
        """Send the request; `on_done` gets its response once, after every progress event."""
        ...


def cancelled_response(request: Request) -> ErrorResponse:
    """Return the `cancelled` error of a request that never ran (the one the backend gives)."""
    return ErrorResponse(action=request.action, error=CancelledError().body())


class PooledCall:
    """The state of one call on a pool; thread-safe.

    The pool thread calls `begin` before running the request and `finish` after it. The GUI
    thread calls `cancel` (a queued call is taken off its pool by `take_off`) and `abandon`
    (after the pools were cleared, on close). `deliver` hands the response to the GUI thread;
    it is called exactly once per call, never under the lock.

    A call that ran is `running` until the GUI thread delivers its answer (`mark_delivered`
    comes before the sender's `on_done`), so a channel is not freed while an answer is still on
    its way. A call that never ran (taken off its pool, cleared on close, cancelled before it
    started) is `done` at once: nothing of it can still run.
    """

    def __init__(
        self,
        request: Request,
        take_off: Callable[[], bool],
        deliver: Callable[["PooledCall", Response], None],
    ) -> None:
        """Start queued; `take_off` removes the call from its pool, `deliver` completes it."""
        self.request = request
        self.flag = CancelFlag()
        self._take_off = take_off
        self._deliver = deliver
        self._lock = threading.Lock()
        self._state: CallState = CALL_QUEUED
        # Whether the backend answered (the answer may not be delivered yet).
        self._answered = False

    @property
    def state(self) -> CallState:
        """Return whether the call is queued, running (or answered, not delivered yet) or done."""
        with self._lock:
            return self._state

    def begin(self) -> bool:
        """Move a queued call to running; return False if it must not run.

        A call cancelled after it was dequeued but before it started completes here as
        `cancelled`, without running the request.
        """
        with self._lock:
            if self._state != CALL_QUEUED:
                return False
            cancelled = self.flag.is_cancelled()
            self._state = CALL_DONE if cancelled else CALL_RUNNING
            self._take_off = _already_off
        if cancelled:
            self._deliver(self, cancelled_response(self.request))
        return not cancelled

    def finish(self, response: Response) -> None:
        """Hand the backend's answer of a running call to the GUI thread."""
        with self._lock:
            self._answered = True
        self._deliver(self, response)

    def mark_delivered(self) -> None:
        """Note on the GUI thread that the answer reached its sender (before `on_done` runs)."""
        with self._lock:
            self._state = CALL_DONE

    def cancel(self) -> None:
        """Set the cancel flag; a queued call taken off its pool completes at once as `cancelled`."""
        self._stop(abandoning=False)

    def abandon(self) -> None:
        """Cancel after the pools were cleared: a call still queued will never run, so it completes now."""
        self._stop(abandoning=True)

    def _stop(self, *, abandoning: bool) -> None:
        with self._lock:
            if self._state == CALL_DONE or self._answered:
                return
            self.flag.cancel()
            dropped = self._state == CALL_QUEUED and (abandoning or self._take_off())
            if dropped:
                self._state = CALL_DONE
                self._take_off = _already_off
        if dropped:
            self._deliver(self, cancelled_response(self.request))


def _already_off() -> bool:
    """`take_off` of a call that left its queue: nothing to take off (and no reference to its runnable)."""
    return False


class DoneHandle:
    """Handle of a call that has already completed."""

    @property
    def state(self) -> CallState:
        """Return "done"."""
        return CALL_DONE

    def cancel(self) -> None:
        """Do nothing: the call has completed."""


class _CallbackSink:
    def __init__(self, on_progress: ProgressCallback | None) -> None:
        self._on_progress = on_progress

    def emit(self, event: ProgressEvent) -> None:
        if self._on_progress is not None:
            self._on_progress(event)


class SyncExecutor:
    """Runs each request inline, on the calling thread (view-model tests; deterministic, no Qt)."""

    def __init__(self, backend: RequestRunner) -> None:
        """Send requests to this backend; `sent` records every request in order."""
        self._backend = backend
        self.sent: list[Request] = []

    def submit(
        self,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None = None,
        on_started: StartedCallback | None = None,
    ) -> CallHandle:
        """Run the request now; the callbacks are called before this returns."""
        self.sent.append(request)
        if on_started is not None:
            on_started()
        response = self._backend.execute(request, _CallbackSink(on_progress), CancelFlag())
        on_done(response)
        return DoneHandle()


def _active(call: CallHandle | None) -> bool:
    return call is not None and call.state != CALL_DONE


class _Sequenced:
    """Sends per key, numbered; only the latest send of a key reaches its callbacks.

    A call cancelled while queued is `done` at once, but its `cancelled` answer is still on its
    way when the next send of the key goes out; numbering drops such stale answers.
    """

    def __init__(self, executor: Executor) -> None:
        self._executor = executor
        self._calls: dict[Hashable, CallHandle] = {}
        self._latest: dict[Hashable, int] = {}
        self._sequence = 0

    def _submit(
        self,
        key: Hashable,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None,
        on_started: StartedCallback | None,
    ) -> CallHandle:
        self._sequence += 1
        number = self._sequence
        self._latest[key] = number
        progress: ProgressCallback | None = None
        if on_progress is not None:
            progress = partial(self._progress, key, number, on_progress)
        started: StartedCallback | None = None
        if on_started is not None:
            started = partial(self._started, key, number, on_started)
        done = partial(self._done, key, number, on_done)
        call = self._executor.submit(request, done, progress, started)
        self._calls[key] = call
        return call

    def _current(self, key: Hashable, number: int) -> bool:
        return self._latest.get(key) == number

    def _done(self, key: Hashable, number: int, on_done: DoneCallback, response: Response) -> None:
        if self._current(key, number):
            on_done(response)

    def _progress(self, key: Hashable, number: int, on_progress: ProgressCallback, event: ProgressEvent) -> None:
        if self._current(key, number):
            on_progress(event)

    def _started(self, key: Hashable, number: int, on_started: StartedCallback) -> None:
        if self._current(key, number):
            on_started()


class ExclusiveChannel(_Sequenced):
    """One call per key: a second send while the key's call is queued or running is refused."""

    def busy(self, key: Hashable) -> bool:
        """Return whether the key's call is queued or running."""
        return _active(self._calls.get(key))

    def send(
        self,
        key: Hashable,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None = None,
        on_started: StartedCallback | None = None,
    ) -> CallHandle | None:
        """Send the request, or return None (refused) while the key's previous call is active."""
        if self.busy(key):
            return None
        return self._submit(key, request, on_done, on_progress, on_started)


class LatestChannel(_Sequenced):
    """Latest wins: a new send cancels the key's previous call and drops its stale response."""

    def send(
        self,
        key: Hashable,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None = None,
    ) -> CallHandle:
        """Cancel the key's previous call (off its pool if queued), then send this one."""
        previous = self._calls.get(key)
        if previous is not None:
            previous.cancel()
        return self._submit(key, request, on_done, on_progress, None)


class SingleFlightChannel(_Sequenced):
    """The poll: a new tick is not sent while the previous one is queued or running.

    The channel follows the handle's state, not a "busy" flag, so a queued tick that is
    cancelled or taken off its pool releases the channel at once (its stale answer is dropped).
    """

    @property
    def state(self) -> CallState | None:
        """Return the current tick's state ("queued": waiting to refresh; "running": refreshing), or None."""
        tick = self._calls.get(POLL_KEY)
        if tick is None or not _active(tick):
            return None
        return tick.state

    def send(
        self,
        request: Request,
        on_done: DoneCallback,
        on_started: StartedCallback | None = None,
    ) -> CallHandle | None:
        """Send a tick, or return None while the previous tick is queued or running."""
        if _active(self._calls.get(POLL_KEY)):
            return None
        return self._submit(POLL_KEY, request, on_done, None, on_started)

    def cancel(self) -> None:
        """Cancel the current tick, if any."""
        tick = self._calls.get(POLL_KEY)
        if tick is not None:
            tick.cancel()
