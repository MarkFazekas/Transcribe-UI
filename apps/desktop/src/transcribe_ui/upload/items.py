"""Uploading one item: extractions, then one audio object per track its jobs use (architecture §3.3 "Upload").

Per item, each step recorded in its own transaction (`upload/rows.py`), so an interrupted upload
resumes after the last recorded one:

- the source-unchanged check (`upload/sources.py`): the file must still have the size and mtime
  it was probed with, else the item goes back to review (`batches/rereview.py`), which forgets
  its rows and deletes its objects; the check runs again after every read of the file, before
  the outcome is recorded; a retry item's copies need no check (its audio is not read);
- each `extract` job without a stored SRT: ffmpeg extracts the stream, the SRT is stored;
- each audio track without a verified object (`upload/tracks.py`);
- the item moves to `uploaded`.
"""

from dataclasses import dataclass
from pathlib import Path

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.batches.records import StoredItem, StoredJob
from transcribe_ui.batches.rereview import return_item_to_review
from transcribe_ui.upload.constants import UPLOAD_ITEM_STAGE
from transcribe_ui.upload.rows import Extracted, item_progress, items_to_upload, mark_uploaded, record_extraction
from transcribe_ui.upload.sources import SourceChangedError, UploadRun, media_call, require_unchanged
from transcribe_ui.upload.tracks import put_track


@dataclass(frozen=True)
class ItemTally:
    """What the upload did with one item (or with all of them)."""

    uploaded: int = 0
    extracted: int = 0
    sent_back: bool = False


def upload_items(run: UploadRun, batch_id: str) -> ItemTally:
    """Upload every resolved item of the batch not uploaded yet, in selection order, with progress per item.

    Raises:
        CancelledError: Cancelled between two steps (the steps recorded so far are kept).
        DomainError: A media tool, R2 or the database failed; re-sending resumes.
    """
    context = run.context
    waiting = items_to_upload(context, batch_id)
    total = len(waiting)
    uploaded = 0
    extracted = 0
    for done, member in enumerate(waiting, start=1):
        context.check_cancelled()
        tally = upload_item(run, member)
        uploaded += tally.uploaded
        extracted += tally.extracted
        event = _item_event(run.action, done, total, member.payload.path)
        context.progress.emit(event)
    return ItemTally(uploaded=uploaded, extracted=extracted)


def _item_event(action: str, done: int, total: int, path: str) -> ProgressEvent:
    return ProgressEvent(action=action, stage=UPLOAD_ITEM_STAGE, completed=done, total=total, path=path)


def upload_item(run: UploadRun, member: StoredItem) -> ItemTally:
    """Upload the item; send it back to review when its file changed.

    The caller holds the batch's lock and not the runs lock (sending back may take it).

    Raises:
        CancelledError: Cancelled between two steps (the steps recorded so far are kept).
        DomainError: A media tool, R2 or the database failed; re-sending resumes.
    """
    try:
        return _upload(run, member)
    except SourceChangedError:
        return_item_to_review(run.context, member.row.batch_id, member.item_id)
    return ItemTally(sent_back=True)


def _upload(run: UploadRun, member: StoredItem) -> ItemTally:
    done = item_progress(run.context, member.item_id)
    require_unchanged(member)
    extract_jobs = [job for job in member.jobs if job.kind == "extract"]
    to_extract = [job for job in extract_jobs if job.row.job_id not in done.extracted]
    for job in to_extract:
        _extract(run, member, job)
    tracks = [track for track in _tracks(member) if track not in done.tracks]
    for track in tracks:
        run.context.check_cancelled()
        put_track(run, member, track)
    mark_uploaded(run.context, member)
    return ItemTally(uploaded=len(tracks), extracted=len(to_extract))


def _extract(run: UploadRun, member: StoredItem, job: StoredJob) -> None:
    context = run.context
    context.check_cancelled()
    stream = job.payload.stream_index
    assert stream is not None, "a resolved extract job has a stream"
    media = context.ports.media
    source = Path(member.payload.path)
    srt_text = media_call(member, lambda: media.extract(source, stream, context.cancel))
    require_unchanged(member)
    record_extraction(context, member, job.row.job_id, Extracted(stream, srt_text))


def _tracks(member: StoredItem) -> list[int | None]:
    """Return the audio tracks the item's `transcribe` jobs use (None first: an audio file as it is)."""
    used = {job.payload.track_index for job in member.jobs if job.kind == "transcribe"}
    return sorted(used, key=_track_order)


def _track_order(track: int | None) -> int:
    return -1 if track is None else track
