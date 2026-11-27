"""Handlers of `create_retry_batch` and `cleanup_cloud` (architecture §3.3, plan m5 §1 "Retry" and "Cleanup").

`create_retry_batch` runs under the source batch's lock (never waited for), so a cleanup of the
source cannot run meanwhile:

1. the retry batch of this source and job set is looked up (`cleanup/origins.py`); one that is
   not `uploading` any more is returned as it is (`past_upload`);
2. otherwise the source and the jobs are checked (`cleanup/planning.py`; refused with
   `audio_deleted` once the source's audio is cleaned up), and the retry batch is created in
   `uploading` under an id R2 holds nothing under (`cleanup/retry.py`);
3. under the retry batch's lock it then runs the resumable upload path of `run_upload`
   (`upload/services.py` `continue_upload`): the copy variant copies each audio object server
   side and verifies it, and the manifest step publishes the manifest (or holds it during a run).

The retry batch is committed before anything is copied, so an interruption at any point leaves a
retry batch that repeating the request (or `run_upload` on the retry batch) resumes; no second
retry batch is created for the same source and job set.

`cleanup_cloud` takes the runs lock (bounded wait), then the batch's lock (never waited for; lock
order runs → batch), and holds both to the end, so no run can start and no other request can
change the batch between the checks and the last delete. A batch this database knows is checked
(`cleanup/targets.py`) and cleaned up (`cleanup/known.py`); another one goes to
`cleanup/unknown.py` (Q28). No R2 or Runpod call is made inside a database transaction.
"""

from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.classes import (
    CleanupCloudRequest,
    CleanupCloudResponse,
    CreateRetryBatchRequest,
    CreateRetryBatchResponse,
)
from transcribe_ui.cleanup.known import cleanup_known
from transcribe_ui.cleanup.origins import ExistingRetry, retry_target
from transcribe_ui.cleanup.planning import origin_of
from transcribe_ui.cleanup.retry import retry_batch_id, retry_response
from transcribe_ui.cleanup.targets import check_target, load_target, missing_confirmations
from transcribe_ui.cleanup.unknown import cleanup_unknown
from transcribe_ui.runs.cloud import open_cloud
from transcribe_ui.upload.services import continue_upload


def create_retry_batch(request: CreateRetryBatchRequest, context: RequestContext) -> CreateRetryBatchResponse:
    """Create (or find) the retry batch of the source and job set, then copy its audio and publish its manifest.

    Raises:
        BatchBusyError: Another request works on the source or the retry batch.
        BatchNotFoundError: There is no source batch.
        AudioDeletedError: The source's audio was cleaned up (only when a retry batch is to be created).
        WrongPhaseError: The source has no results yet.
        RetryJobInvalidError: A job is not a failed or no-speech transcribe job of the source.
        DomainError: R2, Runpod or the database failed, or the request was cancelled; repeating
            the request resumes the retry batch.
    """
    state = context.ports.state
    origin = origin_of(request)
    with state.locks.batch(request.batch_id):
        with state.session() as session:
            target = retry_target(session, origin, request)
        created = not isinstance(target, ExistingRetry)
        if isinstance(target, ExistingRetry) and not target.uploading:
            return retry_response(context, request, target.batch_id, None, created=created)
        with open_cloud(context) as cloud:
            retry_id = retry_batch_id(context, cloud.store, target)
            with state.locks.batch(retry_id):
                report = continue_upload(context, cloud, retry_id, request.action)
    return retry_response(context, request, retry_id, report, created=created)


def cleanup_cloud(request: CleanupCloudRequest, context: RequestContext) -> CleanupCloudResponse | NeedsConfirmation:
    """Delete the batch's audio or all its cloud data, or ask for the confirmations this needs.

    Raises:
        RunsBusyError: Another request held the runs lock for the whole wait (nothing changed).
        BatchBusyError: Another request works on the batch (nothing changed).
        WrongPhaseError: The scope is not possible in the batch's phase.
        RetryCopyingError: A retry batch of the batch still copies its audio.
        ResultsPendingError: `audio` while a job has no result.
        ManifestUnreadableError: An unknown batch's manifest cannot be read.
        RunActiveError: A run is active and the batch may be visible to it (nothing changed).
        ActivityUnknownError: The run activity cannot be checked (nothing changed).
        DomainError: R2 failed or the request was cancelled; repeating it resumes.
    """
    state = context.ports.state
    locks = state.locks
    with locks.runs(context.cancel.is_cancelled), locks.batch(request.batch_id):
        with state.session() as session:
            target = load_target(session, request.batch_id)
        if target is None:
            return cleanup_unknown(request, context)
        check_target(request, target)
        needed = missing_confirmations(request, target)
        if needed:
            return NeedsConfirmation(action=request.action, confirmations=needed)
        return cleanup_known(request, context, target)
