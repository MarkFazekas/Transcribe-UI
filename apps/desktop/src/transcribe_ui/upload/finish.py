"""The end of a batch's upload: the manifest step, or `transcribed` for an extract-only batch (§3.3).

Once every item is uploaded, a batch with `transcribe` jobs goes through the M5.9 manifest step
(`ready`, or held back while a run is active). A batch with only `extract` jobs has nothing for
the worker: it moves `uploading` → `transcribed` without a manifest. If it still has a pending
withdrawal of a leftover manifest (its transcribe jobs were excluded or re-reviewed after an
interrupted manifest step), the withdrawal is finished first, under the runs lock with no run
active, so no leftover stays in R2 for a batch that will never publish again.
"""

from dataclasses import dataclass

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.classes import ActivitySource
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.errors import RunActiveError
from transcribe_ui.state.database import compare_and_set_phase
from transcribe_ui.upload.publish import manifest_step
from transcribe_ui.upload.responses import UploadOutcome
from transcribe_ui.upload.rows import has_transcribe_jobs, items_in_review
from transcribe_ui.upload.steps import finish_withdrawal


@dataclass(frozen=True)
class Finish:
    """Where the end of the upload left the batch."""

    outcome: UploadOutcome
    items_in_review: tuple[str, ...] = ()
    activity: tuple[ActivitySource, ...] = ()


def finish_upload(context: RequestContext, cloud: CloudAccess, batch_id: str) -> Finish:
    """Publish the manifest (or hold it), or finish an extract-only batch; nothing while items are in review.

    The caller holds the batch's lock and not the runs lock; every item not in review is uploaded.

    Raises:
        RunsBusyError: The runs lock stayed held for the whole wait (nothing was changed).
        RunActiveError: An extract-only batch's pending withdrawal cannot be finished during a run.
        ActivityUnknownError: The cloud could not be checked (the manifest is held).
        StorageError: R2 failed (the manifest is held, or the withdrawal stays pending).
    """
    waiting = items_in_review(context, batch_id)
    if waiting:
        return Finish("items_in_review", items_in_review=waiting)
    if not has_transcribe_jobs(context, batch_id):
        _finish_extract_only(context, cloud, batch_id)
        return Finish("transcribed")
    step = manifest_step(context, cloud, batch_id)
    if step.outcome == "held":
        return Finish("held", activity=step.activity)
    return Finish("ready")


def _finish_extract_only(context: RequestContext, cloud: CloudAccess, batch_id: str) -> None:
    state = context.ports.state
    with state.session() as session:
        pending = load_batch(session, batch_id).pending_withdrawal is not None
    if not pending:
        _to_transcribed(context, batch_id)
        return
    with state.locks.runs(context.cancel.is_cancelled):
        activity = check_activity(state, cloud, context.ports.clock.now())
        if activity.active:
            raise RunActiveError(activity.sources)
        finish_withdrawal(context, cloud.store, batch_id)
        _to_transcribed(context, batch_id)


def _to_transcribed(context: RequestContext, batch_id: str) -> None:
    with context.ports.state.session() as session:
        now = context.ports.clock.now()
        compare_and_set_phase(session, batch_id, "uploading", "transcribed", now=now, also_set={"manifest_held": False})
