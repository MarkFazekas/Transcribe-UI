"""What the stops of `terminate_pod` and `cancel_queued_pod` record on the local run of the pod.

A pod a queued pod deployed is recorded on the open run that recorded that queued pod (the run
follows it should its stop not show, and stopping it by id ends the run); a pod read that shows
the run's pod marks it seen; the cancel's acknowledgement and the first empty lookup of a
vanished queued pod are kept on the run (`QueuedTarget` reads them back).
"""

import logging
from dataclasses import replace
from functools import partial

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import QueuedPod
from transcribe_ui.runs.errors import runpod_call
from transcribe_ui.runs.halting import mark_seen, run_hooks
from transcribe_ui.runs.records import open_run_id, read_payload, run_statuses, update_run
from transcribe_ui.runs.stopping import KnownPod, QueuedHooks, QueuedTarget

logger = logging.getLogger(__name__)


def follow_deployed(
    context: RequestContext,
    run_id: str | None,
    queued_pod_id: str,
    pod: KnownPod,
    handled: frozenset[str],
) -> None:
    """Record the pod a stopped queued pod deployed on its local run, before that pod is terminated.

    Only for an open run that recorded this queued pod, and whose recorded pod (if any) is one of
    the pods this stop handles: a pod the run followed before (another pod under its name, say)
    is never replaced by one this stop terminates. Should the terminate not be verified, the run
    then follows the pod (`running`) instead of losing it (after a stop of several pods, the
    first whose stop did not show).
    """
    state = context.ports.state
    open_id = open_run_id(state, run_id)
    if open_id is None:
        return
    payload = read_payload(state, open_id)
    recorded = payload.pod_id
    if payload.queued_pod_id != queued_pod_id or recorded == pod.pod_id:
        return
    if recorded is not None and recorded not in handled:
        return
    changes = {"pod_id": pod.pod_id, "pod_seen": pod.seen, "pod_deployed_at": pod.deployed_at}
    update_run(state, open_id, context.ports.clock.now(), "running", changes)


def mark_run_pod_seen(context: RequestContext, run_id: str | None, pod_id: str) -> None:
    """Record that a pod read showed the pod the local run recorded (a later 404 then means gone)."""
    if run_id is not None and run_id in run_statuses(context.ports.state):
        mark_seen(context.ports.state, run_id, context.ports.clock, pod_id)


def adopt_deployed(context: RequestContext, pods: PodsApi, run_id: str | None, pod_id: str) -> None:
    """Record a pod on the open local run whose queued pod deployed it (the run recorded no pod yet), as seen.

    So that stopping the pod by its id (`terminate_pod`) ends the run, and an unverified stop
    leaves the run following the pod.

    Best effort: a queued pod that cannot be read (logged) skips the recording.
    """
    state = context.ports.state
    open_id = open_run_id(state, run_id)
    if open_id is None:
        return
    payload = read_payload(state, open_id)
    queued_pod_id = payload.queued_pod_id
    if queued_pod_id is None or payload.pod_id is not None:
        return
    try:
        entry = runpod_call(lambda: pods.get_queued_pod(queued_pod_id))
    except CancelledError:
        raise
    except DomainError as error:
        # Best effort: the pod is terminated anyway; the run then follows its queued pod.
        logger.info("Run %s: its queued pod could not be read (%s); its pod is not recorded", open_id, error.code)
        return
    if entry is None or entry.pod_id != pod_id:
        return
    changes = {"pod_id": pod_id, "pod_seen": True, "pod_deployed_at": entry.resolved_at}
    update_run(state, open_id, context.ports.clock.now(), "running", changes)


def queued_stop(context: RequestContext, run_id: str | None, entry: QueuedPod) -> tuple[QueuedTarget, QueuedHooks]:
    """Return the queued pod to cancel as read, with what its local run recorded, and the hooks that record the stop.

    A local run that is open and recorded this queued pod keeps the stop's acknowledgement and
    empty lookups (the settle rule needs two); without a queue `expiresAt` in the read, the run's
    is used. Without such a run the stop is not tracked: a queued pod gone after the cancel with
    no pod listed under its name counts as cancelled at once (`QueuedTarget.vanish`).
    """
    state = context.ports.state
    queued_id = entry.queued_pod_id
    follow = partial(follow_deployed, context, run_id, queued_id)
    target = QueuedTarget.read_from(entry)
    open_id = open_run_id(state, run_id)
    payload = None if open_id is None else read_payload(state, open_id)
    if open_id is None or payload is None or payload.queued_pod_id != queued_id:
        return target, QueuedHooks(on_deployed=follow, on_seen=partial(mark_run_pod_seen, context, run_id))
    tracked = replace(
        target,
        expires_at=target.expires_at or payload.queue_expires_at,
        acknowledged_at=payload.cancel_acknowledged_at,
        first_empty_lookup_at=payload.first_empty_lookup_at,
        tracked=True,
    )
    return tracked, run_hooks(state, open_id, context.ports.clock, follow)
