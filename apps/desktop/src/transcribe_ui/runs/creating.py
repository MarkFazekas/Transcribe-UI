"""Recording a run and requesting its pod (plan m5 §1 "Create calls are never re-sent", "Pod create body").

The run row is committed before `POST /pods`, so a lost answer never loses the run (the run's
lease is written in between, `runs/starting.py`). The POST is
sent once: a refusal (Runpod answered, nothing was created) makes the run `launch_failed`; an
ambiguous failure (timeout, 5xx, connection drop after sending) is never re-sent. Instead the
name is looked up once in both lists and what is found is adopted; otherwise the run stays
`launching` for `refresh_status` and `reconcile_cloud` to resolve (`runs/settling.py`).
"""

from collections.abc import Callable
from datetime import datetime
from functools import partial

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import Clock
from transcribe_ui.runpod.bodies import CreatePod
from transcribe_ui.runpod.errors import AmbiguousCreateError, RunpodApiError
from transcribe_ui.runpod.pods import PodsApi
from transcribe_ui.runpod.wire import Pod, QueuedPod
from transcribe_ui.runs.adopting import Launched, look_up
from transcribe_ui.runs.errors import launch_refusal
from transcribe_ui.runs.pod_body import NewRun
from transcribe_ui.runs.records import update_run
from transcribe_ui.state.store import LocalState

# What the single POST gave: the pod, the queued pod, or the error (never re-sent).
type Sent = Pod | QueuedPod | RunpodApiError | CancelledError


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
    run_id = run.run_id
    sent = _send(pods, body, partial(_record_sending, state, run_id, clock))
    now = clock.now()
    if isinstance(sent, Pod):
        changes = {"pod_id": sent.pod_id, "pod_seen": True, "posted_at": now}
        started = update_run(state, run_id, now, "running", changes)
        return Launched("pod", adopted=False, run=started)
    if isinstance(sent, QueuedPod):
        changes = {"queued_pod_id": sent.queued_pod_id, "posted_at": now}
        changes.update({"pod_id": sent.pod_id, "pod_deployed_at": sent.resolved_at})
        waiting = update_run(state, run_id, now, "queued", changes)
        return Launched("queued", adopted=False, run=waiting)
    if isinstance(sent, AmbiguousCreateError):
        update_run(state, run_id, now, changes={"posted_at": now})
        return look_up(state, pods, run_id, now)
    end_launch_failed(state, run_id, now, posted=True)
    # Cancelled before an attempt: only a 429 or a failed connection came before, so nothing exists.
    raise sent if isinstance(sent, CancelledError) else launch_refusal(run_id, sent)


def end_launch_failed(state: LocalState, run_id: str, now: datetime, *, posted: bool) -> None:
    """End the run as `launch_failed`: nothing was created (`posted`: its pod request ended now)."""
    changes: dict[str, object] = {"ended_at": now, "end_reason": "launch_failed"}
    if posted:
        changes["posted_at"] = now
    update_run(state, run_id, now, "launch_failed", changes)


def _send(pods: PodsApi, body: CreatePod, before_attempt: Callable[[], None]) -> Sent:
    try:
        return pods.create(body, before_attempt)
    except (RunpodApiError, CancelledError) as error:
        return error


def _record_sending(state: LocalState, run_id: str, clock: Clock) -> None:
    """Record that an attempt of the pod request is about to be sent (the settle time counts from the latest)."""
    now = clock.now()
    update_run(state, run_id, now, changes={"sending_at": now})
