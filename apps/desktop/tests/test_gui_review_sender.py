"""The review page's `SerialSender` on a fake executor whose calls the test starts and answers (plan m6 M6.8).

Deterministic: a call's state (queued, running, done), its start signal and its answer are set
by hand, so each order the pools can produce is replayed exactly.
"""

from dataclasses import dataclass, field

from transcribe_ui.api.classes import ErrorResponse, ProgressEvent, Request, Response
from transcribe_ui.batches.classes import GetBatchRequest, ReviewBatchRequest
from transcribe_ui.gui.constants import CALL_DONE, CALL_QUEUED, CALL_RUNNING, CallState
from transcribe_ui.gui.executor import (
    CallHandle,
    DoneCallback,
    ProgressCallback,
    StartedCallback,
    cancelled_response,
)
from transcribe_ui.gui.review.sender import SerialSender

# The batch of the requests.
BATCH_ID = "batch_20261127_130000"
REVIEW = ReviewBatchRequest(action="review_batch", batch_id=BATCH_ID)
# Two actions (any request does; the sender only orders them).
FIRST = GetBatchRequest(action="get_batch", batch_id=BATCH_ID)
SECOND = GetBatchRequest(action="get_batch", batch_id="batch_20261127_130001")


@dataclass
class FakeCall:
    """A call the test moves along by hand."""

    request: Request
    on_done: DoneCallback
    on_started: StartedCallback | None
    state: CallState = CALL_QUEUED
    cancelled: bool = False

    def cancel(self) -> None:
        """Set the flag (a queued call would be taken off its pool; the test answers it)."""
        self.cancelled = True

    def start(self) -> None:
        """Leave the queue and tell the sender (as the bridge does, later, on the GUI thread)."""
        self.state = CALL_RUNNING
        if self.on_started is not None:
            self.on_started()

    def answer(self, response: Response | None = None) -> None:
        """Deliver the answer (`cancelled` when none is given)."""
        self.state = CALL_DONE
        self.on_done(cancelled_response(self.request) if response is None else response)


@dataclass
class FakeExecutor:
    """Records each call; nothing runs until the test says so."""

    calls: list[FakeCall] = field(default_factory=list)

    def submit(
        self,
        request: Request,
        on_done: DoneCallback,
        _on_progress: ProgressCallback | None = None,
        on_started: StartedCallback | None = None,
    ) -> CallHandle:
        """Record the call."""
        call = FakeCall(request, on_done, on_started)
        self.calls.append(call)
        return call


@dataclass
class Answers:
    """The answers the sender passed on, by request action."""

    received: list[str] = field(default_factory=list)

    def __call__(self, response: Response) -> None:
        """Record an answer."""
        cancelled = isinstance(response, ErrorResponse)
        self.received.append(f"{response.action}{' cancelled' if cancelled else ''}")


def _ignore(_event: ProgressEvent | None = None) -> None:
    """Follow of a review nobody watches."""


def _sender() -> tuple[SerialSender, FakeExecutor, Answers]:
    executor = FakeExecutor()
    return SerialSender(executor), executor, Answers()


def test_a_review_that_began_keeps_the_job() -> None:
    """A review whose call left its queue (its start signal not delivered yet) holds the job: the action waits."""
    sender, executor, answers = _sender()
    sender.send_review(REVIEW, answers, (_ignore, _ignore))
    review = executor.calls[0]
    review.state = CALL_RUNNING
    sender.send_action(FIRST, answers)
    assert (review.cancelled, len(executor.calls), sender.reviewing(started_only=True)) == (False, 1, True)
    review.answer(cancelled_response(REVIEW))
    assert [call.request for call in executor.calls] == [REVIEW, FIRST]


def test_stop_then_the_answer_arrives() -> None:
    """Stop drops a running review: it no longer counts as checking, holds the lock until it answers, and its answer goes nowhere."""
    sender, executor, answers = _sender()
    sender.send_review(REVIEW, answers, (_ignore, _ignore))
    review = executor.calls[0]
    review.start()
    sender.drop_reviews()
    held = (review.cancelled, sender.reviewing(), sender.reviewing(started_only=True))
    review.answer()
    assert held == (True, False, True)
    assert (answers.received, sender.reviewing(started_only=True)) == ([], False)


def test_a_second_action_goes_before_the_queued_review() -> None:
    """Apply pre-empts a queued review; a second action sent while the review waits again goes before it too."""
    sender, executor, answers = _sender()
    sender.send_review(REVIEW, answers, (_ignore, _ignore))
    first_review = executor.calls[0]
    sender.send_action(FIRST, answers)
    first_review.answer()
    applied = executor.calls[1]
    sender.send_action(SECOND, answers)
    applied.answer(cancelled_response(FIRST))
    removed = executor.calls[2]
    removed.answer(cancelled_response(SECOND))
    again = executor.calls[3]
    again.start()
    again.answer(cancelled_response(REVIEW))
    order = [call.request for call in executor.calls]
    assert (first_review.cancelled, order) == (True, [REVIEW, FIRST, SECOND, REVIEW])
    assert answers.received == ["get_batch cancelled", "get_batch cancelled", "review_batch cancelled"]
