"""Recording a run and requesting its pod (plan m5 §1 "Create calls are never re-sent", "Pod create body").

The run row is committed before `POST /pods`, so a lost answer never loses the run. The POST is
sent once: a refusal (Runpod answered, nothing was created) makes the run `launch_failed`; an
ambiguous failure (timeout, 5xx, connection drop after sending) is never re-sent. Instead the
name is looked up once in both lists and what is found is adopted; otherwise the run stays
`launching` for `refresh_status` and `reconcile_cloud` to resolve (`runs/settling.py`).
"""

from dataclasses import dataclass
from datetime import datetime

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.bodies import CreatePod
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import Pod, QueuedPod
from transcribe_ui.runs.errors import launch_refusal
from transcribe_ui.runs.pod_body import NewRun
from transcribe_ui.runs.records import update_run
from transcribe_ui.runs.responses import LaunchOutcome, RunInfo
from transcribe_ui.runs.settling import Listed, resolve_launching
from transcribe_ui.state.store import LocalState

# What the single POST gave: the pod, the queued pod, or the error (never re-sent).
type Sent = Pod | QueuedPod | RunpodApiError | CancelledError


@dataclass(frozen=True)
class Launched:
    """How the pod request ended, and the run as it is now."""

    outcome: LaunchOutcome
    adopted: bool
    run: RunInfo


def request_pod(state: LocalState, pods: PodsApi, run: NewRun, body: CreatePod, clock: Clock) -> Launched:
    """Send the one `POST /pods` of the recorded run and record its outcome.

    The time recorded as `posted_at` is read after the POST ended, so it is no earlier than the
    moment the request was sent (the settle time counts from it).

    Raises:
        LaunchRefusedError: Runpod refused the request (the run is `launch_failed`).
        DomainError: The key was rejected, lacks a scope, or Runpod could not be reached before
            anything was sent (the run is `launch_failed`).
        CancelledError: Cancelled before the request was sent (the run is `launch_failed`).
    """
    sent = _send(pods, body)
    now = clock.now()
    run_id = run.run_id
    if isinstance(sent, Pod):
        changes = {"pod_id": sent.pod_id, "posted_at": now}
        started = update_run(state, run_id, now, "running", changes)
        return Launched("pod", adopted=False, run=started)
    if isinstance(sent, QueuedPod):
        changes = {"queued_pod_id": sent.queued_pod_id, "pod_id": sent.pod_id, "posted_at": now}
        waiting = update_run(state, run_id, now, "queued", changes)
        return Launched("queued", adopted=False, run=waiting)
    if isinstance(sent, AmbiguousCreateError):
        update_run(state, run_id, now, changes={"posted_at": now})
        return _look_up(state, pods, run_id, now)
    update_run(state, run_id, now, "launch_failed", {"posted_at": now})
    # Cancelled before an attempt: only a 429 or a failed connection came before, so nothing exists.
    raise sent if isinstance(sent, CancelledError) else launch_refusal(run_id, sent)


def _send(pods: PodsApi, body: CreatePod) -> Sent:
    try:
        return pods.create(body)
    except (RunpodApiError, CancelledError) as error:
        return error


def _look_up(state: LocalState, pods: PodsApi, run_id: str, now: datetime) -> Launched:
    """Look the run's name up once after an ambiguous failure; adopt what is found."""
    try:
        listed = Listed.read(pods)
    except RunpodApiError:
        listed = Listed((), ())  # the lists failed too: later status checks resolve the run
    followed = resolve_launching(state, run_id, listed, now)
    if followed.status == "running":
        return Launched("pod", adopted=True, run=followed)
    if followed.status == "queued":
        return Launched("queued", adopted=True, run=followed)
    return Launched("unconfirmed", adopted=False, run=followed)
