"""What must hold before `launch_worker` records a run: no run active, no withdrawal or cleanup left pending."""

from sqlalchemy import func, select

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.constants import CLEANUP_PENDING_PATH
from transcribe_ui.runs.errors import CleanupPendingError, RunActiveError
from transcribe_ui.state.errors import BatchBusyError
from transcribe_ui.state.models import BatchRow
from transcribe_ui.state.store import LocalState
from transcribe_ui.upload.classes import HeldPublication
from transcribe_ui.upload.manifest import withdrawing_batch_ids
from transcribe_ui.upload.publish import publish_held_manifests


def require_no_run(context: RequestContext, cloud: CloudAccess) -> None:
    """Refuse while any sign of a worker run exists (a straggler included).

    Raises:
        RunActiveError: A run is active (`run_active`).
        ActivityUnknownError: The run activity cannot be checked (`cloud_unreachable`).
    """
    activity = check_activity(context.ports.state, cloud, context.ports.clock.now())
    if activity.active:
        raise RunActiveError(activity.sources)


def publish_before_launch(context: RequestContext, cloud: CloudAccess) -> HeldPublication:
    """Publish the held manifests and finish pending withdrawals before a launch; refuse when either cannot be done.

    Raises:
        RunActiveError: `publish_held_manifests` found a sign of a run.
        BatchBusyError: A withdrawal stays pending because its batch is busy.
    """
    published = publish_held_manifests(context, cloud)
    if published.activity:
        raise RunActiveError(published.activity)
    require_withdrawals_done(context)
    return published


def require_withdrawals_done(context: RequestContext) -> None:
    """Refuse while a leftover manifest's withdrawal is still pending (its batch lock was busy).

    A worker started now could read that leftover; re-sending the launch finishes it first.

    Raises:
        BatchBusyError: The first such batch (`batch_busy`, re-sendable).
    """
    with context.ports.state.session() as session:
        pending = withdrawing_batch_ids(session)
    if pending:
        raise BatchBusyError(pending[0])


def require_no_pending_cleanup(state: LocalState) -> None:
    """Refuse while a cloud cleanup was recorded but not finished (`cleanup_cloud` interrupted).

    Its batch's manifest or audio may still be in R2 although the batch counts as cleaned up, so
    a worker started now could transcribe it; repeating the cleanup finishes it.

    Raises:
        CleanupPendingError: The batches whose cleanup must be repeated (`cleanup_pending`).
    """
    marked = func.json_extract(BatchRow.payload, CLEANUP_PENDING_PATH).is_not(None)
    query = select(BatchRow.batch_id).where(marked).order_by(BatchRow.batch_id)
    with state.session() as session:
        pending = list(session.scalars(query))
    if pending:
        raise CleanupPendingError(pending)
