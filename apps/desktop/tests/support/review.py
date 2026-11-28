"""The review page's view-models and window over the batch tree (plan m6 M6.8)."""

import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from support.batch_backend import run_ok
from support.batches import FIRST_BATCH_ID, batch_ports, batch_request, build_batch_tree
from support.jobs import RecordingOutlet, RecordingRunner
from support.parity import Leg
from support.upload import CREATE
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ProgressEvent, Request, Response
from transcribe_ui.api.ports import CancelToken, ProgressSink
from transcribe_ui.batches.responses import BatchReview, JobView, ReviewFile
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.executor import Executor, SyncExecutor
from transcribe_ui.gui.review.uploads import UploadTracker
from transcribe_ui.gui.review.viewmodel import ReviewModel
from transcribe_ui.media.classes import AudioTrack, SubtitleStream

# The requests that bring the first batch to review: every file in English and Hungarian (the
# silent video only in Hungarian), then the review started.
SETUP = (CREATE, batch_request("start_review"))


@dataclass
class StatusOutlet(RecordingOutlet):
    """A recording outlet that also keeps the status bar's messages."""

    statuses: list[str] = field(default_factory=list)

    def show_status(self, text: str) -> None:
        """Record a status bar message."""
        self.statuses.append(text)


@dataclass
class ReviewArea:
    """The review page's view-model over a leg, with its runner, outlet and activity panel."""

    leg: Leg
    runner: RecordingRunner
    outlet: StatusOutlet
    activity: Activity
    model: ReviewModel
    executor: Executor


def review_area(leg: Leg, executor: Callable[[RecordingRunner], Executor] = SyncExecutor) -> ReviewArea:
    """Return the review view-model over the leg's backend, shown (on `SyncExecutor` unless given)."""
    runner = RecordingRunner(leg.backend)
    outlet = StatusOutlet()
    sender = executor(runner)
    activity = Activity(sender)
    model = ReviewModel(sender, outlet, UploadTracker(activity, outlet))
    model.visibility_changed(shown=True)
    return ReviewArea(leg, runner, outlet, activity, model, sender)


@contextmanager
def review_leg(root: Path) -> Iterator[Leg]:
    """Return a leg on the batch tree whose first batch is in review (closed afterwards)."""
    leg = Leg.create(root, None, build_batch_tree, batch_ports)
    with closing(leg):
        for document in SETUP:
            run_ok(leg, document)
        yield leg


# How long a paused call waits for the test at most, in seconds.
PAUSE_LIMIT_S = 30


class PausingRunner:
    """Runs requests on a backend and records them; pauses a call once at the first progress event `pause_at` matches.

    The call waits on its pool thread until `resume` (or `PAUSE_LIMIT_S`), so the test can act on
    the GUI (Cancel, close the window) while the request is between two of its steps.
    """

    def __init__(self, backend: Backend, pause_at: Callable[[ProgressEvent], bool]) -> None:
        """Run on `backend`; pause at the first event `pause_at` accepts."""
        self._backend = backend
        self._pause_at = pause_at
        self.requests: list[Request] = []
        self.paused = threading.Event()
        self._resumed = threading.Event()

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Record the request, then run it with a sink that pauses once."""
        self.requests.append(request)
        return self._backend.execute(request, _PausingSink(self, progress), cancel)

    def resume(self) -> None:
        """Let the paused call go on (and every later one pass)."""
        self._resumed.set()

    def note(self, event: ProgressEvent) -> None:
        """Pause on the first matching event."""
        if not self.paused.is_set() and self._pause_at(event):
            self.paused.set()
            self._resumed.wait(PAUSE_LIMIT_S)


@dataclass
class _PausingSink:
    runner: PausingRunner
    inner: ProgressSink | None

    def emit(self, event: ProgressEvent) -> None:
        if self.inner is not None:
            self.inner.emit(event)
        self.runner.note(event)


def large_review(count: int) -> BatchReview:
    """Return a review of `count` video files, each with two audio tracks, an English text subtitle and one English job.

    Built without validation (`model_construct`), so a test of tens of thousands of rows starts fast.
    """
    tracks = tuple(
        AudioTrack.model_construct(index=index, codec="aac", language=None, title=None, default=False, channels=2)
        for index in range(2)
    )
    stream = SubtitleStream.model_construct(
        index=0, codec="subrip", kind="text", language="eng", title=None, default=False, forced=False
    )
    files = tuple(_large_file(number, tracks, stream) for number in range(count))
    return BatchReview.model_construct(
        batch_id=FIRST_BATCH_ID,
        phase="reviewing",
        items_in_review=count,
        items_unresolved=0,
        complete=True,
        subtitle_view=files,
        track_view=(),
        unresolved=(),
    )


def _large_file(number: int, tracks: tuple[AudioTrack, ...], stream: SubtitleStream) -> ReviewFile:
    item_id = f"01M8EE0VG0{number:016d}"
    job = JobView.model_construct(
        language="en",
        kind="transcribe",
        mode="normal",
        track_index=0,
        stream_index=None,
        status="pending",
        resolved=True,
        result_error=None,
        result_error_code=None,
        flagged=False,
    )
    return ReviewFile.model_construct(
        item_id=item_id,
        path=f"/media/Series/episode-{number}.mkv",
        name=f"episode-{number}.mkv",
        kind="video",
        phase="reviewed",
        problem=None,
        audio_tracks=tracks,
        subtitle_streams=(stream,),
        jobs=(job,),
        suggestions=(),
    )
