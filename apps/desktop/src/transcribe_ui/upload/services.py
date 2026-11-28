"""`run_upload`: start or resume a batch's upload (architecture §3.3 "Upload", plan m5 §1 "Upload").

Under the batch's lock (never waited for), with R2 opened once (the Runpod key is not needed):

1. a batch in `reviewing` (every item resolved) moves to `uploading` once R2 holds nothing under
   its id (`upload/start.py`);
2. leftovers of an interruption go: the batch's temp folder is emptied, its open multipart
   uploads are aborted, and the orphan sweep deletes audio objects no item records
   (`upload/sweep.py`);
3. every resolved item not uploaded yet is uploaded (`upload/items.py`), one recorded step at a
   time, with progress per item and per part, and the cancel token checked between steps;
4. once no item is in review, the manifest step publishes the manifest, or an extract-only
   batch moves to `transcribed` (`upload/finish.py`).

No R2 or ffmpeg call is made inside a database transaction, and the runs lock is only
taken through its bounded wait (lock order runs → batch). Cancel or kill at any point leaves a
state that re-sending the request resumes, without duplicate or missing objects.
"""

from dataclasses import dataclass

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.batches.records import stored_items, summary_of
from transcribe_ui.runs.cloud import open_store
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.classes import RunUploadRequest
from transcribe_ui.upload.constants import REVIEWED_BATCH
from transcribe_ui.upload.finish import Finish, finish_upload
from transcribe_ui.upload.items import ItemTally, upload_items
from transcribe_ui.upload.responses import RunUploadResponse
from transcribe_ui.upload.sources import UploadRun
from transcribe_ui.upload.start import checked_phase, start_upload
from transcribe_ui.upload.sweep import clear_leftovers
from transcribe_ui.upload.temp import clear_temp, temp_dir


def run_upload(request: RunUploadRequest, context: RequestContext) -> RunUploadResponse:
    """Upload the batch's items, then publish its manifest (or finish an extract-only batch).

    Raises:
        BatchBusyError: Another request works on the batch.
        WrongPhaseError: The batch is neither `reviewing` nor `uploading`.
        ReviewIncompleteError: The batch is `reviewing` with an item not resolved.
        BatchIdTakenError: R2 already holds objects under the batch's id (nothing was changed).
        CancelledError: Cancelled; what was done is recorded and re-sending resumes.
    """
    batch_id = request.batch_id
    with context.ports.state.locks.batch(batch_id):
        phase = checked_phase(context, batch_id)
        store = open_store(context)
        if phase == REVIEWED_BATCH:
            start_upload(context, store, batch_id)
        report = continue_upload(context, store, batch_id, request.action)
        return _response(context, batch_id, report)


@dataclass(frozen=True)
class UploadReport:
    """What the upload did: the items' tally, where it ended, and the sweep's deletions (None: put off)."""

    tally: ItemTally
    finish: Finish
    deleted: int | None


def continue_upload(context: RequestContext, store: ObjectStore, batch_id: str, action: str) -> UploadReport:
    """Run steps 2-4 on an `uploading` batch: leftovers, every item not uploaded yet, the end (§3.3).

    The caller holds the batch's lock and not the runs lock; `create_retry_batch` uses it for
    its retry batch (copy items) as `run_upload` does for every batch.

    Raises:
        CancelledError: Cancelled; what was done is recorded and re-sending resumes.
        DomainError: A media tool, R2 or the database failed; re-sending resumes.
    """
    state = context.ports.state
    clear_temp(state.paths, batch_id)
    deleted = clear_leftovers(context, store, batch_id)
    folder = temp_dir(state.paths, batch_id)
    run = UploadRun(context, store, folder, action)
    tally = upload_items(run, batch_id)
    finish = finish_upload(context, store, batch_id)
    return UploadReport(tally, finish, deleted)


def _response(context: RequestContext, batch_id: str, report: UploadReport) -> RunUploadResponse:
    tally = report.tally
    finish = report.finish
    with context.ports.state.session() as session:
        row = load_batch(session, batch_id)
        now = context.ports.clock.now()
        summary = summary_of(session, row, stored_items(session, batch_id), now=now)
    return RunUploadResponse(
        batch=summary,
        outcome=finish.outcome,
        items_in_review=finish.items_in_review,
        uploaded_objects=tally.uploaded,
        extracted_subtitles=tally.extracted,
        deleted_objects=report.deleted or 0,
        sweep_skipped=report.deleted is None,
    )
