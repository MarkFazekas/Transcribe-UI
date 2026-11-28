"""What the review page loads: the job (`get_batch`) and its review views (`review_batch`).

Qt-free. The job gives the step, the allowed actions and the folders; the review probes the
files waiting for review (progress per file; a cancel stops between two files and the next
review continues with the rest) and returns the views narrowed by the page's filters. A reload
loads the job first and reviews only while the job allows it (a job past its upload, or with
every file uploaded, has nothing to review). Every review is followed by a load of the job,
since a probe can change what the job allows.

Late answers never change the page: each open of a job and each change of the filters starts a
new generation, and an answer of an earlier generation (or about another job) is dropped. A
review is not sent while the job's upload runs (both hold the job's lock); the page waits for it.
A hidden page sends nothing: a load asked for meanwhile marks the page stale, and it loads once
when it is shown again.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Protocol

from transcribe_ui.api.classes import ErrorResponse, ProgressEvent, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.batches.classes import GetBatchRequest, ReviewBatchRequest, ReviewFilter
from transcribe_ui.batches.responses import BatchDetail, BatchReview, GetBatchResponse, ReviewBatchResponse
from transcribe_ui.gui.executor import Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.review.constants import DETAIL_KEY, REVIEW_ACTION, ReviewState
from transcribe_ui.gui.review.sender import SerialSender


class Settled(Protocol):
    """Told that the views of a job changed; `tell` whether the other pages should hear it."""

    def __call__(self, batch_id: str, *, tell: bool) -> None:
        """Note the change."""


@dataclass
class Loaded:
    """What the page shows of its job: the job, its review views, the check's progress, and the errors of both loads."""

    detail: BatchDetail | None = None
    detail_error: ErrorView | None = None
    review: BatchReview | None = None
    review_error: ErrorView | None = None
    state: ReviewState = "shown"
    # The probe's latest progress event while a review runs.
    probe: ProgressEvent | None = None


@dataclass
class Visibility:
    """Whether the page is shown, and whether it must load again once it is."""

    shown: bool = False
    stale: bool = False


def _review_allowed(loaded: Loaded) -> bool:
    detail = loaded.detail
    return detail is None or REVIEW_ACTION in detail.summary.actions


def _without_review(loaded: Loaded, *, waiting: bool) -> Loaded:
    """Return what the page shows when no review can run: the upload holds the job, or nothing waits for review."""
    if waiting:
        loaded.state = "waiting_upload"
        return loaded
    return Loaded(detail=loaded.detail, detail_error=loaded.detail_error)


class ReviewLoader:
    """The page's job and its review views, with the probe's progress and the errors of both loads."""

    def __init__(
        self,
        executor: Executor,
        sender: SerialSender,
        notify: Callable[[], None],
        upload_busy: Callable[[str], bool],
    ) -> None:
        """Load the job through `executor` and reviews through `sender`; `notify` follows every change."""
        self._channel = LatestChannel(executor)
        self._sender = sender
        self._notify = notify
        self._upload_busy = upload_busy
        self.batch_id: str | None = None
        self.filters = ReviewFilter()
        self.loaded = Loaded()
        self.page = Visibility()
        # Told the job's id after a review answered (the probe may have changed the job: `tell`), or
        # after the views went away (nothing to review); the page then drops what they no longer list.
        self.on_settled: Settled | None = None
        self.generation = 0
        # A review waits for the job's next load (`reload`).
        self._review_after = False

    def open_batch(self, batch_id: str) -> bool:
        """Show a job (another job starts empty, without filters); return whether it is another job."""
        self.generation += 1
        other = batch_id != self.batch_id
        if other:
            self.batch_id = batch_id
            self.filters = ReviewFilter()
            self.loaded = Loaded()
        return other

    def reload(self) -> None:
        """Load the job, then its review views while the job allows a review."""
        self._review_after = True
        self.load_detail()

    def load_review(self) -> None:
        """Check the files waiting for review and load the views (a review in flight is replaced)."""
        batch_id = self.batch_id
        if batch_id is None or self._held():
            return
        waiting = self._upload_busy(batch_id)
        if waiting or not _review_allowed(self.loaded):
            self._sender.drop_reviews()
            self.loaded = _without_review(self.loaded, waiting=waiting)
            self._notify()
            settled = self.on_settled
            if settled is not None and not waiting:
                settled(batch_id, tell=False)
            return
        request = ReviewBatchRequest(action="review_batch", batch_id=batch_id, filters=self.filters)
        generation = self.generation
        self.loaded.state = "queued"
        self.loaded.probe = None
        reviewed = partial(self._reviewed, batch_id, generation)
        started = partial(self._followed, generation, None)
        follow = (partial(self._followed, generation), started)
        self._sender.send_review(request, reviewed, follow)
        self._notify()

    def stop(self) -> None:
        """Stop checking (the next review continues with the files not checked yet)."""
        if self._sender.reviewing():
            self._sender.drop_reviews()
            self.loaded.state = "stopped"
            self.loaded.probe = None
            self._notify()

    def load_detail(self) -> None:
        """Load the job (step, allowed actions, files)."""
        batch_id = self.batch_id
        if batch_id is not None and not self._held():
            request = GetBatchRequest(action="get_batch", batch_id=batch_id)
            self._channel.send(DETAIL_KEY, request, partial(self._detailed, batch_id))

    def _held(self) -> bool:
        """Return whether the page is hidden: nothing is sent, and it loads once it is shown again."""
        if self.page.shown:
            return False
        self.page.stale = True
        self._review_after = False
        return True

    def _followed(self, generation: int, event: ProgressEvent | None) -> None:
        """Note a review's progress event (None: its call left its pool's queue and runs now)."""
        if generation != self.generation:
            return
        self.loaded.state = "checking"
        if event is not None:
            self.loaded.probe = event
        self._notify()

    def _reviewed(self, batch_id: str, generation: int, response: Response) -> None:
        if batch_id != self.batch_id or generation != self.generation:
            return
        loaded = self.loaded
        loaded.probe = None
        loaded.state = "shown"
        if isinstance(response, ErrorResponse):
            cancelled = response.error.code == ERROR_CANCELLED
            loaded.state = "stopped" if cancelled else "shown"
            loaded.review_error = None if cancelled else error_view(response)
        if isinstance(response, ReviewBatchResponse):
            loaded.review = response.review
            loaded.review_error = None
        self._notify()
        self.load_detail()
        settled = self.on_settled
        if settled is not None and isinstance(response, ReviewBatchResponse):
            settled(batch_id, tell=True)

    def _detailed(self, batch_id: str, response: Response) -> None:
        if batch_id != self.batch_id:
            return
        if isinstance(response, GetBatchResponse):
            self.loaded.detail = response.batch
            self.loaded.detail_error = None
        elif isinstance(response, ErrorResponse):
            self.loaded.detail_error = error_view(response)
        self._notify()
        if self._review_after:
            self._review_after = False
            self.load_review()
