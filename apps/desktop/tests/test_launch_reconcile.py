"""A lost pod request, the settling rule and `reconcile_cloud` (plan m5 M5.11)."""

from datetime import timedelta

import httpx
import pytest

from support.launch import (
    LAUNCH,
    PODS,
    POST,
    QUEUED_PODS,
    TERMINATE,
    error_code,
    launch,
    launch_confirmed,
    launch_run,
    ok_as,
    pods_of,
    posts,
    run_ids,
    run_status,
    send,
    set_up_cloud,
)
from support.runpod_api import Failure
from support.runs import OTHER_RUN_ID, RUN_ID, RunsWorld, app_pod_name, seed_ready
from transcribe_ui.runs.constants import ERROR_RUN_ACTIVE, LAUNCH_LOOKUP_GAP_S, LAUNCH_SETTLE_S
from transcribe_ui.runs.responses import ReconcileCloudResponse, TerminatePodResponse

# How long the lost pod stays out of the lists.
LIST_LAG = timedelta(minutes=2)
# Past that lag, but before the settle time.
AFTER_LAG = timedelta(minutes=3)
# Settle time after the pod request.
SETTLE = timedelta(seconds=LAUNCH_SETTLE_S)
# Gap between the two empty lookups that end a launch.
GAP = timedelta(seconds=LAUNCH_LOOKUP_GAP_S)
# Less than that gap.
SHORT = timedelta(seconds=LAUNCH_LOOKUP_GAP_S / 2)
# A Runpod outage of one attempt: the create is ambiguous, nothing was created.
SERVER_ERROR = Failure(status=httpx.codes.SERVICE_UNAVAILABLE)
# Past the name deadline of the test pods (3 hours).
PAST_DEADLINE = timedelta(hours=4)
# A pod that is not one of the app's.
FOREIGN_POD = "somebody-elses-pod"


@pytest.fixture
def ready(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def _reconcile(world: RunsWorld) -> ReconcileCloudResponse:
    return ok_as(send(world, "reconcile_cloud"), ReconcileCloudResponse)


def _entries(reconciled: ReconcileCloudResponse) -> list[tuple[str, str, bool]]:
    return [(entry.kind, entry.role, entry.overdue) for entry in reconciled.entries]


def _fail_lookups_until_failed(world: RunsWorld) -> None:
    for step in (SETTLE, GAP):
        world.cloud.clock.advance(step)
        _reconcile(world)


def test_lost_create_response_is_adopted_later(ready: RunsWorld) -> None:
    """The POST took effect but its answer was lost and the pod is listed late: no second POST, adopted later."""
    api = ready.cloud.api
    api.settings.list_lag = LIST_LAG
    api.fail(POST, PODS, Failure(lost_response=True))
    launched = launch(ready)
    assert (launched.outcome, launched.run.status, posts(ready)) == ("unconfirmed", "launching", 1)
    assert error_code(launch_confirmed(ready)) == ERROR_RUN_ACTIVE
    ready.cloud.clock.advance(AFTER_LAG)
    reconciled = _reconcile(ready)
    adopted = reconciled.runs[0]
    assert ((adopted.status, adopted.pod_id), _entries(reconciled), posts(ready)) == (
        ("running", pods_of(ready)[0][0]),
        [("pod", "current", False)],
        1,
    )


def test_lost_response_found_at_once_is_adopted(ready: RunsWorld) -> None:
    """Without list lag the single lookup after the lost answer finds the queued pod at once."""
    ready.cloud.settings.capacity = False
    ready.cloud.api.fail(POST, PODS, Failure(lost_response=True))
    launched = launch(ready)
    assert (launched.outcome, launched.adopted, launched.run.status) == ("queued", True, "queued")
    assert ready.cloud.api.count("GET", QUEUED_PODS) >= 1


def test_launching_run_fails_only_after_settling(ready: RunsWorld) -> None:
    """No pod ever: `launch_failed` only after the settle time and two empty lookups at least a gap apart."""
    ready.cloud.api.fail(POST, PODS, SERVER_ERROR)
    assert launch_run(ready).status == "launching"
    run_id = run_ids(ready)[0]
    seen = []
    for step in (AFTER_LAG, SETTLE, SHORT, GAP):
        ready.cloud.clock.advance(step)
        _reconcile(ready)
        seen.append(run_status(ready, run_id))
    assert (seen, posts(ready)) == (["launching", "launching", "launching", "launch_failed"], 1)


def test_late_pod_after_launch_failed_is_a_straggler(ready: RunsWorld) -> None:
    """A pod that shows up after the run failed is a straggler and refuses launches until it is stopped."""
    ready.cloud.api.fail(POST, PODS, SERVER_ERROR)
    name = launch_run(ready).pod_name
    run_id = run_ids(ready)[0]
    _fail_lookups_until_failed(ready)
    pod_id = str(ready.cloud.api.pods.add(name)["id"])
    reconciled = _reconcile(ready)
    assert (_entries(reconciled), reconciled.attention, run_status(ready, run_id)) == (
        [("pod", "straggler", False)],
        True,
        "launch_failed",
    )
    assert error_code(send(ready, LAUNCH)) == ERROR_RUN_ACTIVE
    assert send(ready, TERMINATE, pod_id=pod_id).status == "needs_confirmation"
    stopped = ok_as(send(ready, TERMINATE, pod_id=pod_id, confirm=True), TerminatePodResponse)
    assert (stopped.run_id, stopped.local_status, pods_of(ready)) == (run_id, "launch_failed", [])
    assert _reconcile(ready).entries == ()


def test_reconcile_reports_overdue_and_unknown_entries(runs_world: RunsWorld) -> None:
    """Entries of no local run are stragglers; past the name deadline they are overdue too; others are ignored."""
    api = runs_world.cloud.api
    now = runs_world.now()
    api.pods.add(app_pod_name(RUN_ID, now))
    # A queue that outlives the name deadline (only a hand-made request does that).
    api.queued_pods.add(app_pod_name(OTHER_RUN_ID, now), expires_at=now + PAST_DEADLINE * 2)
    api.pods.add(FOREIGN_POD)
    assert _entries(_reconcile(runs_world)) == [("pod", "straggler", False), ("queued_pod", "straggler", False)]
    runs_world.cloud.clock.advance(PAST_DEADLINE)
    assert _entries(_reconcile(runs_world)) == [("pod", "straggler", True), ("queued_pod", "straggler", True)]
