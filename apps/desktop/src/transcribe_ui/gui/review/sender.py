"""The review page's batch requests, one at a time (architecture §3.1 "GUI" "Review and upload").

Qt-free. `review_batch`, `resolve_review` and `exclude_items` each hold the job's batch lock,
which the backend never waits for (`batch_busy`), so the page sends them one after the other.
Reviews are latest-wins: a new review replaces a review still waiting and cancels a running one,
whose answer is then dropped (it stops between two files; the next review continues with the
rest). An action (Apply, Remove) waits for the call before it and is never dropped.
"""

from dataclasses import dataclass, replace
from functools import partial

from transcribe_ui.api.classes import ProgressEvent, Request, Response
from transcribe_ui.gui.constants import CALL_QUEUED
from transcribe_ui.gui.executor import CallHandle, DoneCallback, Executor, ProgressCallback, StartedCallback

# A review's progress events and the start of its call (it left its pool's queue).
type ReviewFollow = tuple[ProgressCallback, StartedCallback]


@dataclass
class _Queued:
    request: Request
    on_done: DoneCallback
    on_progress: ProgressCallback | None
    review: bool
    on_started: StartedCallback | None = None
    # The call left its pool's queue and runs.
    started: bool = False


def _is_review(queued: _Queued | None) -> bool:
    return queued is not None and queued.review


class SerialSender:
    """Sends the page's batch requests one at a time; a newer review replaces an older one."""

    def __init__(self, executor: Executor) -> None:
        """Send through `executor`."""
        self._executor = executor
        self._waiting: list[_Queued] = []
        self._running: _Queued | None = None
        self._call: CallHandle | None = None
        # The running review was replaced or stopped: its answer goes nowhere.
        self._dropped = False

    def reviewing(self, *, started_only: bool = False) -> bool:
        """Return whether a review is running or waiting (`started_only`: whether one runs on a pool thread now).

        A review still queued behind other calls does not hold the job yet: the page stays usable.
        """
        if started_only:
            # A started review holds the job's lock until it answers, even when dropped.
            return self._review_started()
        live = _is_review(self._running) and not self._dropped
        return live or any(queued.review for queued in self._waiting)

    def acting(self) -> bool:
        """Return whether an action (Apply, Remove) is running or waiting."""
        running = self._running is not None and not _is_review(self._running)
        return running or any(not queued.review for queued in self._waiting)

    def send_review(self, request: Request, on_done: DoneCallback, follow: ReviewFollow) -> None:
        """Send a review once the call before it answered; an older review is dropped (a running one cancelled).

        `follow` gets the review's progress events and is told when it leaves its pool's queue.
        """
        self.drop_reviews()
        on_progress, on_started = follow
        queued = _Queued(request, on_done, on_progress, review=True, on_started=on_started)
        self._waiting.append(queued)
        self._next()

    def send_action(self, request: Request, on_done: DoneCallback) -> None:
        """Send an action once the calls before it answered.

        A review still queued on its pool (not started) does not hold the action back: it is taken
        off its queue and sent again after the action, so Apply or Remove answers at once.
        """
        # Actions go before the reviews still waiting, so no action waits for a review's turn.
        reviews = [index for index, queued in enumerate(self._waiting) if queued.review]
        position = reviews[0] if reviews else len(self._waiting)
        self._waiting.insert(position, _Queued(request, on_done, None, review=False))
        running = self._running
        call = self._call
        queued_review = _is_review(running) and not self._review_started()
        if running is not None and queued_review and call is not None and not self._dropped:
            self._waiting.append(replace(running, started=False))
            self._dropped = True
            call.cancel()
        self._next()

    def drop_reviews(self) -> None:
        """Drop the reviews still waiting and cancel a running one (none of them answers)."""
        self._waiting = [queued for queued in self._waiting if not queued.review]
        call = self._call
        if _is_review(self._running) and call is not None and not self._dropped:
            self._dropped = True
            call.cancel()

    def _review_started(self) -> bool:
        """Return whether the running review left its pool's queue (its call's state, or its started signal)."""
        running = self._running
        if running is None or not running.review:
            return False
        call = self._call
        return running.started or (call is not None and call.state != CALL_QUEUED)

    def _next(self) -> None:
        if self._running is not None or not self._waiting:
            return
        queued = self._waiting.pop(0)
        self._running = queued
        self._dropped = False
        progress = None if queued.on_progress is None else partial(self._follow, queued)
        started = None
        if queued.on_started is not None:
            started = partial(self._follow, queued, None)
        done = partial(self._done, queued)
        call = self._executor.submit(queued.request, done, progress, started)
        if self._running is queued:
            # Not answered yet (`SyncExecutor` answers inside `submit`).
            self._call = call

    def _follow(self, queued: _Queued, event: ProgressEvent | None) -> None:
        """Pass a progress event of the running review on (None: its call left the queue)."""
        if event is None:
            # Recorded even for a dropped review: it holds the job's lock until it answers.
            queued.started = True
        if queued is not self._running or self._dropped:
            return
        if event is None and queued.on_started is not None:
            queued.on_started()
        elif event is not None and queued.on_progress is not None:
            queued.on_progress(event)

    def _done(self, queued: _Queued, response: Response) -> None:
        if queued is not self._running:
            return
        dropped = self._dropped
        self._running = None
        self._call = None
        self._dropped = False
        if not dropped:
            queued.on_done(response)
        self._next()
