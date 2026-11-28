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
   side and verifies it, and the manifest step publishes the manifest.

The retry batch is committed before anything is copied, so an interruption at any point leaves a
retry batch that repeating the request (or `run_upload` on the retry batch) resumes; no second
retry batch is created for the same source and job set.

`cleanup_cloud` takes the runs lock (bounded wait), then the batch's lock (never waited for; lock
order runs → batch). A batch whose id this database knows is checked (`cleanup/targets.py`),
confirmed and then, when R2 holds no manifest under the id or one recognised as this database's
(architecture §3.9 "Known batches"), cleaned up (`cleanup/known.py`): the runs lock is held through the checks and the transaction that records
the cleanup with its `cleanup_pending` marker, then released before the deletes (the marker keeps
every launch out, the batch lock and the phase the manifest steps); the batch lock is held to the
end. A batch it does not know (another computer's, possibly under an id this database also uses)
goes to `cleanup/unknown.py` (Q28) and keeps both locks to the end, since nothing local would
keep a launch out. Only R2 is needed (no Runpod key), and no R2 call is made inside a database
transaction.
"""

from contextlib import ExitStack

from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.classes import (
    CleanupCloudRequest,
    CleanupCloudResponse,
    CreateRetryBatchRequest,
    CreateRetryBatchResponse,
)
from transcribe_ui.cleanup.confirmations import missing_confirmations
from transcribe_ui.cleanup.known import cleanup_known
from transcribe_ui.cleanup.manifests import known_in_cloud
from transcribe_ui.cleanup.origins import ExistingRetry, retry_target
from transcribe_ui.cleanup.planning import origin_of
from transcribe_ui.cleanup.retry import retry_batch_id, retry_response, still_uploading
from transcribe_ui.cleanup.targets import check_target, load_target
from transcribe_ui.cleanup.unknown import cleanup_unknown
from transcribe_ui.runs.cloud import open_store
from transcribe_ui.upload.services import continue_upload


def create_retry_batch(request: CreateRetryBatchRequest, context: RequestContext) -> CreateRetryBatchResponse:
    """Create (or find) the retry batch of the source and job set, then copy its audio and publish its manifest.

    Raises:
        BatchBusyError: Another request works on the source or the retry batch.
        BatchNotFoundError: There is no source batch.
        AudioDeletedError: The source's audio was cleaned up (only when a retry batch is to be created).
        WrongPhaseError: The source has no results yet.
        RetryJobInvalidError: A job is not a failed or no-speech transcribe job of the source.
        DomainError: R2 or the database failed, or the request was cancelled; repeating the
            request resumes the retry batch.
    """
    state = context.ports.state
    origin = origin_of(request)
    with state.locks.batch(request.batch_id):
        with state.session() as session:
            target = retry_target(session, origin, request)
        created = not isinstance(target, ExistingRetry)
        if isinstance(target, ExistingRetry) and not target.uploading:
            return retry_response(context, request, target.batch_id, None, created=created)
        store = open_store(context)
        retry_id = retry_batch_id(context, store, target)
        with state.locks.batch(retry_id):
            if not still_uploading(context, retry_id):
                return retry_response(context, request, retry_id, None, created=created)
            report = continue_upload(context, store, retry_id, request.action)
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
        RunActiveError: A live run lease lists the batch (nothing changed).
        DomainError: R2 failed or the request was cancelled; repeating it resumes.
    """
    state = context.ports.state
    locks = state.locks
    with ExitStack() as runs_held:
        runs_held.enter_context(locks.runs(context.cancel.is_cancelled))
        with locks.batch(request.batch_id):
            with state.session() as session:
                target = load_target(session, request.batch_id)
            if target is None:
                return cleanup_unknown(request, context)
            check_target(request, target)
            needed = missing_confirmations(request, target)
            if needed:
                return NeedsConfirmation(action=request.action, confirmations=needed)
            if not known_in_cloud(state, open_store(context), request.batch_id):
                return cleanup_unknown(request, context)
            return cleanup_known(request, context, target, runs_held.close)
