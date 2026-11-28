"""`launch_worker`: start one worker for every pending manifest (architecture §3.7).

Everything runs under the runs lock, so no manifest step, cleanup or other launch can change the
run activity or the scope in between: an interrupted cloud cleanup refuses (`cleanup_pending`), as
does the activity check (a live run or straggler), then the
cloud setup check, the plan and its fingerprint (a plan other than the confirmed one asks for
confirmation again), `publish_held_manifests` (which also finishes pending withdrawals of leftover
manifests; one left pending because its batch is busy refuses the launch), the run row (committed
before the pod request) and the single `POST /pods`. No database transaction is open across a
Runpod or R2 call.
"""

from transcribe_ui.api.classes import NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.client import RunpodClient
from transcribe_ui.runpod.inventory import require_cloud_setup
from transcribe_ui.runs.classes import LaunchWorkerRequest
from transcribe_ui.runs.cloud import open_cloud_client
from transcribe_ui.runs.creating import request_pod
from transcribe_ui.runs.errors import NothingToLaunchError, RunActiveError
from transcribe_ui.runs.guards import require_no_pending_cleanup, require_no_run, require_withdrawals_done
from transcribe_ui.runs.payloads import RunPayload
from transcribe_ui.runs.planning import confirmation, current_plan
from transcribe_ui.runs.pod_body import Decided, NewRun, create_body, name_run
from transcribe_ui.runs.records import insert_run
from transcribe_ui.runs.responses import LaunchWorkerResponse
from transcribe_ui.upload.publish import publish_held_manifests


def launch_worker(request: LaunchWorkerRequest, context: RequestContext) -> LaunchWorkerResponse | NeedsConfirmation:
    """Start the worker on the confirmed scope, or ask for confirmation of the current one.

    Raises:
        CleanupPendingError: A cloud cleanup was interrupted; it must be repeated first.
        RunActiveError: A run is active or a straggler pod exists (`run_active`; nothing changed).
        ActivityUnknownError: The run activity cannot be checked (`cloud_unreachable`).
        BatchBusyError: A leftover manifest's withdrawal stays pending (its batch is busy); no
            run was recorded, re-send.
        DomainError: The cloud setup is missing or outdated, nothing is pending, R2 failed, or
            Runpod refused the pod (the run is then `launch_failed`).
        RunsBusyError: Another request held the runs lock for the whole wait.
    """
    state = context.ports.state
    runs_lock = state.locks.runs(context.cancel.is_cancelled)
    with runs_lock, open_cloud_client(context) as (cloud, client):
        require_no_pending_cleanup(state)
        require_no_run(context, cloud)
        setup = require_cloud_setup(client)
        plan = current_plan(state, cloud.store, client.catalog, test_mode=request.test_mode)
        if not plan.scope.pending_jobs:
            raise NothingToLaunchError
        fingerprint = request.confirm_fingerprint
        if fingerprint != plan.fingerprint:
            return confirmation(plan, changed=fingerprint is not None)
        published = publish_held_manifests(context, cloud)
        if published.activity:
            raise RunActiveError(published.activity)
        require_withdrawals_done(context)
        return _launch(request, context, client, Decided(plan, setup, published))


def _launch(
    request: LaunchWorkerRequest,
    context: RequestContext,
    client: RunpodClient,
    decided: Decided,
) -> LaunchWorkerResponse:
    ports = context.ports
    plan = decided.plan
    now = ports.clock.now()
    run_id = ports.ids.new_id()
    named = name_run(run_id, now, request.wait_s, plan.max_runtime_s)
    payload = RunPayload(
        max_runtime_s=plan.max_runtime_s,
        test_mode=request.test_mode,
        wait_s=request.wait_s,
        queue_expires_at=named.expires_at,
        name_deadline=named.deadline,
        covered_batch_ids=decided.covered(),
        fingerprint=plan.scope.fingerprint,
        pending_duration_s=plan.scope.pending_duration_s,
        estimate=plan.cost,
        weighted_known_duration_s=decided.followed_duration_s(),
        unknown_runtime_s=decided.unknown_runtime_s(),
    )
    run = NewRun(run_id, named.pod_name, payload)
    insert_run(ports.state, run_id, named.pod_name, now, payload)
    body = create_body(run, decided.setup)
    launched = request_pod(ports.state, client.pods, run, body, ports.clock)
    return LaunchWorkerResponse(
        outcome=launched.outcome,
        adopted=launched.adopted,
        run=launched.run,
        published=decided.published.published,
    )
