"""`launch_worker`: start one worker for every pending manifest (architecture §3.7).

Everything runs under the runs lock, so no cleanup, withdrawal or other launch of this computer
can change the run activity or the scope in between: an interrupted cloud cleanup refuses
(`cleanup_pending`), as does the activity check (a live run, a straggler, another computer's
live lease), then the cloud setup check, the plan and its fingerprint (a plan other than the
confirmed one asks for confirmation again), the run row (committed before the pod request), the
run's lease (`runs/<run_id>/launch.json`: the covered batches, which
the worker alone processes; a failed write makes the run `launch_failed` before anything is
created) and the single `POST /pods`. No database transaction is open across a
Runpod or R2 call.
"""

from typing import TYPE_CHECKING

from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.inventory import require_cloud_setup
from transcribe_ui.runs.classes import LaunchWorkerRequest
from transcribe_ui.runs.cloud import open_cloud_client
from transcribe_ui.runs.errors import NothingToLaunchError
from transcribe_ui.runs.guards import require_no_pending_cleanup, require_no_run
from transcribe_ui.runs.lease_release import releasing_leases
from transcribe_ui.runs.planning import confirmation, current_plan
from transcribe_ui.runs.pod_body import Decided, NewRun, create_body, name_run
from transcribe_ui.runs.responses import LaunchWorkerResponse
from transcribe_ui.runs.starting import Starting, start_run

if TYPE_CHECKING:
    from transcribe_ui.storage.r2 import ObjectStore


def launch_worker(request: LaunchWorkerRequest, context: RequestContext) -> LaunchWorkerResponse | NeedsConfirmation:
    """Start the worker on the confirmed scope, or ask for confirmation of the current one.

    Raises:
        CleanupPendingError: A cloud cleanup was interrupted; it must be repeated first.
        RunActiveError: A run is active or a straggler pod exists (`run_active`; nothing changed).
        ActivityUnknownError: The run activity cannot be checked (`cloud_unreachable`).
        DomainError: The cloud setup is missing or outdated, nothing is pending, R2 failed, or
            Runpod refused the pod (the run is then `launch_failed`).
        RunsBusyError: Another request held the runs lock for the whole wait.
    """
    state = context.ports.state
    runs_lock = state.locks.runs(context.cancel.is_cancelled)
    with runs_lock, open_cloud_client(context) as (cloud, client), releasing_leases(context):
        require_no_pending_cleanup(state)
        require_no_run(context, cloud)
        setup = require_cloud_setup(client)
        plan = current_plan(state, cloud.store, client.catalog, test_mode=request.test_mode)
        if not plan.scope.pending_jobs:
            raise NothingToLaunchError
        fingerprint = request.confirm_fingerprint
        if fingerprint != plan.fingerprint:
            return confirmation(plan, changed=fingerprint is not None)
        return _launch(request, context, client, Decided(plan, setup), cloud.store)


def _launch(
    request: LaunchWorkerRequest,
    context: RequestContext,
    client: RunpodClient,
    decided: Decided,
    store: "ObjectStore",
) -> LaunchWorkerResponse:
    ports = context.ports
    now = ports.clock.now()
    run_id = ports.ids.new_id()
    named = name_run(run_id, now, request.wait_s, decided.plan.max_runtime_s)
    payload = decided.run_payload(named, wait_s=request.wait_s, test_mode=request.test_mode)
    run = NewRun(run_id, named.pod_name, payload)
    body = create_body(run, decided.setup)
    launched = start_run(context, Starting(store, client.pods), run, body)
    return LaunchWorkerResponse(
        outcome=launched.outcome,
        adopted=launched.adopted,
        run=launched.run,
    )
