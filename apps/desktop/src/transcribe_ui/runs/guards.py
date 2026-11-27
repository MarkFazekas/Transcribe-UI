"""What must hold before `launch_worker` records a run: no run active, no withdrawal left pending."""

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.errors import RunActiveError
from transcribe_ui.state.errors import BatchBusyError
from transcribe_ui.upload.manifest import withdrawing_batch_ids


def require_no_run(context: RequestContext, cloud: CloudAccess) -> None:
    """Refuse while any sign of a worker run exists (a straggler included).

    Raises:
        RunActiveError: A run is active (`run_active`).
        ActivityUnknownError: The run activity cannot be checked (`cloud_unreachable`).
    """
    activity = check_activity(context.ports.state, cloud, context.ports.clock.now())
    if activity.active:
        raise RunActiveError(activity.sources)


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
