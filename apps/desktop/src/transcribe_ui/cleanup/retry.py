"""The steps of `create_retry_batch` (plan m5 §1 "Retry"): reserving the retry batch's id, and the response.

Batch ids are unique only in this database, and a retry batch is created in `uploading`, so
`run_upload` never runs its start check for it: before the batch is created, the same check
(`upload/start.py` `id_in_use`: manifest, objects and open multipart uploads under the id) must
find nothing in R2. An id that R2 holds data under is skipped for the next free one.
"""

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.errors import WrongPhaseError
from transcribe_ui.batches.ids import base_batch_id, free_batch_id
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.batches.records import stored_items, summary_of
from transcribe_ui.cleanup.classes import CreateRetryBatchRequest, CreateRetryBatchResponse
from transcribe_ui.cleanup.constants import CLEANED, RETRY_ID_ATTEMPTS
from transcribe_ui.cleanup.origins import ExistingRetry
from transcribe_ui.cleanup.planning import RetryPlan
from transcribe_ui.cleanup.rows import NewBatch, insert_retry
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.constants import UPLOADING_BATCH
from transcribe_ui.upload.errors import BatchIdTakenError
from transcribe_ui.upload.services import UploadReport
from transcribe_ui.upload.start import id_in_use


def retry_batch_id(context: RequestContext, store: ObjectStore, target: ExistingRetry | RetryPlan) -> str:
    """Return the existing retry batch's id, or create the planned one and return its id.

    Raises:
        BatchIdTakenError: R2 holds data under every id tried (nothing was created).
        StorageError: R2 could not be read (nothing was created).
    """
    if isinstance(target, ExistingRetry):
        return target.batch_id
    return _reserve(context, store, target)


def _reserve(context: RequestContext, store: ObjectStore, plan: RetryPlan) -> str:
    """Create the retry batch under the first id that is free here and in R2; return the id.

    Raises:
        BatchIdTakenError: R2 holds data under every id tried (nothing was created).
        StorageError: R2 could not be read (nothing was created).
    """
    ports = context.ports
    now = ports.clock.now()
    base = base_batch_id(now, ports.local_zone)
    in_r2: set[str] = set()
    candidate = base
    for _ in range(RETRY_ID_ATTEMPTS):
        with ports.state.session() as session:
            candidate = free_batch_id(session, base, frozenset(in_r2))
        if id_in_use(store, candidate):
            in_r2.add(candidate)
            continue
        with ports.state.session() as session:
            # Another request may have taken the id meanwhile (a `create_batch` in the same second).
            if free_batch_id(session, base, frozenset(in_r2)) == candidate:
                insert_retry(session, NewBatch(candidate, now), plan, ports.ids)
                return candidate
    raise BatchIdTakenError(candidate)


def retry_response(
    context: RequestContext,
    request: CreateRetryBatchRequest,
    retry_id: str,
    report: UploadReport | None,
    *,
    created: bool,
) -> CreateRetryBatchResponse:
    """Return the response: the retry batch as stored now and what this request's upload did (None: nothing)."""
    with context.ports.state.session() as session:
        row = load_batch(session, retry_id)
        summary = summary_of(row, stored_items(session, retry_id))
    if report is None:
        return CreateRetryBatchResponse(
            source_batch_id=request.batch_id,
            created=created,
            batch=summary,
            outcome="past_upload",
            copied_objects=0,
            deleted_objects=0,
            sweep_skipped=False,
        )
    return CreateRetryBatchResponse(
        source_batch_id=request.batch_id,
        created=created,
        batch=summary,
        outcome=report.finish.outcome,
        copied_objects=report.tally.uploaded,
        deleted_objects=report.deleted or 0,
        sweep_skipped=report.deleted is None,
        activity=report.finish.activity,
    )


def still_uploading(context: RequestContext, retry_id: str) -> bool:
    """Return whether the retry batch, whose lock the caller now holds, is still `uploading`.

    It may have moved on since it was found (a `run_upload` that finished it); one that was cleaned
    up meanwhile is refused, so nothing is copied into a cleaned prefix.

    Raises:
        WrongPhaseError: The retry batch is `cleaned`.
    """
    with context.ports.state.session() as session:
        phase = load_batch(session, retry_id).phase
    if phase == CLEANED:
        raise WrongPhaseError(retry_id, phase, (UPLOADING_BATCH,))
    return phase == UPLOADING_BATCH
