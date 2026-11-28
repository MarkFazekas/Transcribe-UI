"""What must hold before `launch_worker` records a run: no run active, no cleanup left pending."""

from sqlalchemy import func, select

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.constants import CLEANUP_PENDING_PATH
from transcribe_ui.runs.errors import CleanupPendingError, RunActiveError
from transcribe_ui.state.models import BatchRow
from transcribe_ui.state.store import LocalState


def require_no_run(context: RequestContext, cloud: CloudAccess) -> None:
    """Refuse while any sign of a worker run exists (a straggler included).

    Raises:
        RunActiveError: A run is active (`run_active`).
        ActivityUnknownError: The run activity cannot be checked (`cloud_unreachable`).
    """
    activity = check_activity(context.ports.state, cloud, context.ports.clock.now())
    if activity.active:
        raise RunActiveError(activity.sources)


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
