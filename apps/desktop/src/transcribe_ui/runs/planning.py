"""`plan_launch` and the confirmation of `launch_worker` (plan m5 §1 "Launch scope fingerprint").

The plan is the launch scope (every pending manifest in R2 plus the held ones), the runtime
limit and the cost estimate. `launch_worker` launches only on the scope the user confirmed: it
carries the plan's fingerprint (the scope, the runtime limit and the test mode), and a different
one gives `needs_confirmation` again.
"""

from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.account import CatalogApi
from transcribe_ui.runs.classes import LaunchPlan, PlanLaunchRequest
from transcribe_ui.runs.cloud import open_cloud_client
from transcribe_ui.runs.constants import SECONDS_PER_HOUR
from transcribe_ui.runs.estimate import launch_plan
from transcribe_ui.runs.guards import require_no_pending_cleanup
from transcribe_ui.runs.responses import PlanLaunchResponse
from transcribe_ui.runs.scope import read_launch_scope
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectStore

# Request field that confirms a launch.
CONFIRM_FIELD = "confirm_fingerprint"


def plan_launch(request: PlanLaunchRequest, context: RequestContext) -> PlanLaunchResponse:
    """Return what a worker started now would process, its runtime limit and its cost estimate.

    Reads only (no lock): `launch_worker` computes the plan again under the runs lock.

    Raises:
        CleanupPendingError: A cloud cleanup was interrupted; it must be repeated first.
    """
    require_no_pending_cleanup(context.ports.state)
    with open_cloud_client(context) as (cloud, client):
        state = context.ports.state
        plan = current_plan(state, cloud.store, client.catalog, test_mode=request.test_mode)
    return PlanLaunchResponse(plan=plan)


def current_plan(state: LocalState, store: ObjectStore, catalog: CatalogApi, *, test_mode: bool) -> LaunchPlan:
    """Return the plan of a launch now.

    Raises:
        StorageError: R2 could not be read.
    """
    scope = read_launch_scope(state, store)
    return launch_plan(scope, catalog, test_mode=test_mode)


def confirmation(plan: LaunchPlan, *, changed: bool) -> NeedsConfirmation:
    """Return the confirmation of a launch on this plan (`changed`: it differs from the one confirmed)."""
    scope = plan.scope
    cost = plan.cost
    unknown = sum(1 for batch in scope.batches if not batch.known)
    hours = scope.pending_duration_s / SECONDS_PER_HOUR
    summary = (
        f"Start one worker for {scope.pending_jobs} job(s) in {len(scope.batches)} batch(es), {hours:.1f} h of "
        f"audio; estimated cost {cost.low_usd:.2f}-{cost.high_usd:.2f} USD."
    )
    parts = [summary]
    if unknown:
        parts.append(f"{unknown} of the batches were uploaded from another computer.")
    if changed:
        parts.insert(0, "The plan changed since it was confirmed (the work, the runtime limit or the test mode).")
    details = {"plan": plan.model_dump(mode="json")}
    found = Confirmation(field=CONFIRM_FIELD, message=" ".join(parts), details=details)
    return NeedsConfirmation(action="launch_worker", confirmations=(found,))
