"""The jobs' uploads in the activity panel (architecture §3.1 "GUI" "Review and upload").

Qt-free. Upload on the review page and Upload or Resume upload on the job page (the `run_upload`
signal) start `run_upload` here; it runs in the activity panel (one per job), whose Cancel and
Resume work as for every long request: a cancelled upload is shown as stopped, and Resume sends
the same request again, which continues from the steps already recorded (§3.3 "Upload").

The tracker follows its entries in the panel: the latest progress event per stage (the files done
and the bytes of the current object), each tagged with the call it came from, so a Resume shows
only its own progress. Its listeners hear only about the job that changed. A finished upload emits
`batches_changed`; files it sent back to review (their source changed) are named in the status bar.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from transcribe_ui.api.classes import ProgressEvent, Response
from transcribe_ui.gui.activity import Activity, ActivityEntry
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.executor import CallHandle
from transcribe_ui.gui.review.commands import ReviewOutlet
from transcribe_ui.gui.review.constants import BYTES_STAGE, ITEM_STAGE
from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.upload.classes import RunUploadRequest
from transcribe_ui.upload.responses import RunUploadResponse

# Told the id of the job whose upload changed.
type UploadListener = Callable[[str], None]


@dataclass(frozen=True)
class UploadStatus:
    """A job's upload as the review page shows it."""

    entry: ActivityEntry
    # The latest progress after a file and within the current object, of the entry's current call.
    files: ProgressEvent | None
    part: ProgressEvent | None
    # The answer of the upload that finished last (None while one runs, or after a failure).
    finished: RunUploadResponse | None


@dataclass(frozen=True)
class _Seen:
    state: str
    event: ProgressEvent | None
    call: CallHandle | None


class UploadTracker:
    """Starts the jobs' uploads in the activity panel and follows their progress."""

    def __init__(self, activity: Activity, outlet: ReviewOutlet) -> None:
        """Start uploads in `activity`; `batches_changed` and status messages go to `outlet`."""
        self._activity = activity
        self._outlet = outlet
        self._entries: dict[str, ActivityEntry] = {}
        self._seen: dict[str, _Seen] = {}
        self._events: dict[tuple[str, str], tuple[CallHandle | None, ProgressEvent]] = {}
        self._finished: dict[str, RunUploadResponse] = {}
        self._listeners: list[UploadListener] = []
        activity.subscribe(self._activity_changed)

    def subscribe(self, listener: UploadListener) -> None:
        """Call `listener` with a job's id each time its upload changes."""
        self._listeners.append(listener)

    def start(self, batch_id: str) -> bool:
        """Start (or resume) a job's upload; False when it already runs (the status bar says so)."""
        request = RunUploadRequest(action="run_upload", batch_id=batch_id)
        title = tr("Upload of job {batch_id}").format(batch_id=batch_id)
        entry = self._activity.start(request, title, partial(self._done, batch_id))
        if entry is None:
            self._outlet.show_status(tr("The upload of job {batch_id} is already running.").format(batch_id=batch_id))
            return False
        self._entries[batch_id] = entry
        self._finished.pop(batch_id, None)
        self._activity_changed()
        return True

    def busy(self, batch_id: str) -> bool:
        """Return whether a job's upload is queued or running."""
        entry = self._entries.get(batch_id)
        return entry is not None and entry.can_cancel

    def cancel(self, batch_id: str) -> None:
        """Cancel a job's upload (it stops between two steps; Resume continues)."""
        entry = self._entries.get(batch_id)
        if entry is not None:
            self._activity.cancel(entry)

    def status(self, batch_id: str) -> UploadStatus | None:
        """Return a job's upload as the review page shows it, or None when it has none in this session."""
        entry = self._entries.get(batch_id)
        if entry is None:
            return None
        return UploadStatus(
            entry=entry,
            files=self._event(batch_id, entry, ITEM_STAGE),
            part=self._event(batch_id, entry, BYTES_STAGE),
            finished=self._finished.get(batch_id),
        )

    def _event(self, batch_id: str, entry: ActivityEntry, stage: str) -> ProgressEvent | None:
        kept = self._events.get((batch_id, stage))
        if kept is None or entry.state != "running":
            return None
        call, event = kept
        return event if call is entry.call else None

    def _activity_changed(self) -> None:
        """Note what changed in the tracked entries (each progress event comes with one change)."""
        changed = []
        for batch_id, entry in self._entries.items():
            seen = _Seen(entry.state, entry.last_event, entry.call)
            if seen == self._seen.get(batch_id):
                continue
            self._seen[batch_id] = seen
            event = entry.last_event
            if event is not None:
                self._events[batch_id, event.stage] = (entry.call, event)
            if event is not None and event.stage == ITEM_STAGE:
                # The object of the latest part event is done with its file.
                self._events.pop((batch_id, BYTES_STAGE), None)
            changed.append(batch_id)
        for batch_id in changed:
            self._notify(batch_id)

    def _done(self, batch_id: str, response: Response) -> None:
        if isinstance(response, RunUploadResponse):
            self._finished[batch_id] = response
            count = len(response.items_in_review)
            if count:
                text = ntr(
                    "Job {batch_id}: {count} file is back in review.",
                    "Job {batch_id}: {count} files are back in review.",
                    count,
                )
                self._outlet.show_status(text.format(batch_id=batch_id, count=count))
        # Also after a stop or a failure: the steps recorded so far changed the job.
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, (batch_id,))
        self._notify(batch_id)

    def _notify(self, batch_id: str) -> None:
        for listener in list(self._listeners):
            listener(batch_id)
