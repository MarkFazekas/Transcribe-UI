"""Starting a run (architecture §3.7 "Launch", §3.9 "Run scope"): its row, its lease, then its one pod request.

The run row is committed first, so a lost answer never loses the run. The run's lease
(`runs/<run_id>/launch.json`: the covered batches and the pod name deadline) is written next: the
worker processes only the batches it lists, so it must exist before any pod can start. A failed
write makes the run `launch_failed` before anything is created (an ambiguous one that stored the
object is deleted with the leases of the runs that ended, `runs/lease_release.py`). The leases
are then listed again: another computer that passed its activity check at the same moment wrote
its own lease meanwhile, so a recent lease of another run makes this run `launch_failed` with
`run_active` before anything is created (both computers may back off; Start again then works for
one of them). Then the one `POST /pods` is sent (`runs/creating.py`).
"""

from dataclasses import dataclass

from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.bodies import CreatePod
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runs.adopting import Launched
from transcribe_ui.runs.creating import end_launch_failed, request_pod
from transcribe_ui.runs.errors import RunActiveError
from transcribe_ui.runs.leases import concurrent_leases, lease_sources, write_lease
from transcribe_ui.runs.pod_body import NewRun
from transcribe_ui.runs.records import insert_run
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore


@dataclass(frozen=True)
class Starting:
    """Where a run is started: its lease goes to R2, its pod request to Runpod."""

    store: ObjectStore
    pods: PodsApi


def start_run(context: RequestContext, starting: Starting, run: NewRun, body: CreatePod) -> Launched:
    """Record the run, write its lease, then send its one pod request.

    Raises:
        StorageError: The lease could not be written or the leases listed (the run is
            `launch_failed`; nothing was created).
        RunActiveError: Another computer started a run at the same moment (the run is
            `launch_failed`; nothing was created).
        LaunchRefusedError: Runpod refused the request (the run is `launch_failed`).
        DomainError: The key was rejected, lacks a scope, or Runpod could not be reached before
            anything was sent (the run is `launch_failed`).
        CancelledError: Cancelled before the request was sent (the run is `launch_failed`).
    """
    ports = context.ports
    payload = run.payload
    run_id = run.run_id
    insert_run(ports.state, run_id, run.pod_name, ports.clock.now(), payload)
    covered = payload.covered_batch_ids
    try:
        write_lease(starting.store, run_id, covered, payload.name_deadline)
        concurrent = concurrent_leases(starting.store, run_id, ports.clock.now())
    except StorageError:
        # An ambiguous failure may have stored the lease: the run's ending deletes it (`runs/lease_release.py`).
        end_launch_failed(ports.state, run_id, ports.clock.now(), posted=False)
        raise
    if concurrent:
        end_launch_failed(ports.state, run_id, ports.clock.now(), posted=False)
        raise RunActiveError(lease_sources(concurrent))
    return request_pod(ports.state, starting.pods, run, body, ports.clock)
