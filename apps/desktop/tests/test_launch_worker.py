"""`plan_launch` and `launch_worker` (plan m5 M5.11): the confirmed launch, the pod request, the plan.

Batches are seeded through the state store, their audio in moto; Runpod is the fake API.
"""

from datetime import timedelta

import pytest

from support.batches import FIRST_BATCH_ID
from support.confirm import confirmed_document
from support.launch import (
    FOREIGN_BATCH_ID,
    FOREIGN_SECONDS,
    LAUNCH,
    document,
    launch,
    launch_run,
    ok_as,
    plan_fingerprint,
    plan_of,
    pod_env,
    pods_of,
    posts,
    queued_of,
    run_ids,
    run_row,
    send,
    set_up_cloud,
    write_foreign,
)
from support.runs import READY, RunsWorld, app_pod_name, batch_state, seed_ready
from transcribe_ui.runs.constants import NAME_DEADLINE_MARGIN_S, TEST_MODE_RUNTIME_S, WAIT_DEFAULT_S
from transcribe_ui.runs.responses import LaunchWorkerResponse

# Seconds of the seeded batch's audio (the film's 90 minutes and the podcast's minute).
SEEDED_SECONDS = 5460.0
# A wait for a machine other than the default.
TWO_HOURS_S = 7200
# How far the test pod names' deadline lies after the moment they are built from.
NAME_DEADLINE = timedelta(hours=3)


@pytest.fixture
def ready(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def test_launch_direct(ready: RunsWorld) -> None:
    """With capacity: one pod is created on the published batch; the run follows it."""
    launched = launch(ready, wait_s=TWO_HOURS_S)
    run = launched.run
    assert (launched.outcome, run.status, run.covered_batch_ids) == ("pod", "running", (FIRST_BATCH_ID,))
    assert (batch_state(ready), posts(ready), [run.pod_id]) == (READY, 1, [pod_id for pod_id, _ in pods_of(ready)])


def test_pod_create_body(ready: RunsWorld) -> None:
    """The body names the template (not the image), the GPU, disk, Global Volume, env and the queue."""
    run = launch_run(ready, wait_s=TWO_HOURS_S)
    api = ready.cloud.api
    env = {"TRANSCRIBE_RUN_ID": run.run_id, "MAX_RUNTIME_S": str(run.max_runtime_s), "KEEP_POD_ALIVE": "false"}
    mounts = {"global": [{"volumeId": api.volumes.listed()[0]["id"], "path": "/workspace"}]}
    expires = (ready.now() + timedelta(seconds=TWO_HOURS_S)).isoformat().replace("+00:00", "Z")
    expected = {
        "name": run.pod_name,
        "templateId": api.templates.listed()[0]["id"],
        "gpu": {"id": "NVIDIA RTX A5000", "count": 1, "minCudaVersion": "12.8"},
        "cloud": "SECURE",
        "disk": 60,
        "mounts": mounts,
        "env": env,
        "queue": {"expiresAt": expires},
    }
    assert api.pods.create_bodies == [expected]


def test_name_deadline_covers_the_wait_and_the_runtime(ready: RunsWorld) -> None:
    """The pod name's deadline is the queue's `expiresAt` + `MAX_RUNTIME_S` + 10 minutes, rounded up."""
    run = launch_run(ready)
    payload = run_row(ready, run.run_id)[1]
    latest = ready.now() + timedelta(seconds=WAIT_DEFAULT_S + payload.max_runtime_s + NAME_DEADLINE_MARGIN_S)
    assert latest <= payload.name_deadline < latest + timedelta(minutes=1)
    assert run.pod_name == app_pod_name(run.run_id, payload.name_deadline - NAME_DEADLINE)


def test_launch_queued(ready: RunsWorld) -> None:
    """Without capacity the request is queued (202): the run is `queued` with the queued pod's id."""
    ready.cloud.settings.capacity = False
    launched = launch(ready)
    run = launched.run
    assert (launched.outcome, run.status, [run.queued_pod_id], run.pod_id) == (
        "queued",
        "queued",
        [queued_id for queued_id, _ in queued_of(ready)],
        None,
    )


def test_test_mode_keeps_the_pod_for_two_hours(ready: RunsWorld) -> None:
    """Test mode: `MAX_RUNTIME_S` of 2 h, `KEEP_POD_ALIVE=true`, and a cost for the whole 2 h."""
    plan = plan_of(ready, test_mode=True)
    launch(ready, test_mode=True)
    env = pod_env(ready)
    assert (plan.max_runtime_s, env["MAX_RUNTIME_S"], env["KEEP_POD_ALIVE"]) == (
        TEST_MODE_RUNTIME_S,
        str(TEST_MODE_RUNTIME_S),
        "true",
    )
    assert plan.cost.low_usd > plan.cost.price_per_hour_usd * 2


def test_unconfirmed_launch_changes_nothing(ready: RunsWorld) -> None:
    """Without the fingerprint: `needs_confirmation` with the plan; no run, no pod, nothing published."""
    response = send(ready, LAUNCH)
    assert response.status == "needs_confirmation"
    confirmation = response.confirmations[0]
    expected = plan_of(ready).model_dump(mode="json")
    shown = {key: confirmation.details[key] for key in ("changed", "jobs", "batches", "unknown_batches", "plan")}
    jobs = expected["scope"]["pending_jobs"]
    assert (confirmation.code, confirmation.field) == ("launch_plan", "confirm_fingerprint")
    assert shown == {"changed": False, "jobs": jobs, "batches": 1, "unknown_batches": 0, "plan": expected}
    assert (run_ids(ready), posts(ready), batch_state(ready)[1]) == ([], 0, True)
    # Confirmed as every frontend does: the field set to its `set_to`, the plan's fingerprint.
    resent = ready.leg.backend.execute_json(confirmed_document(document(LAUNCH), response))
    assert (ok_as(resent, LaunchWorkerResponse).outcome, posts(ready)) == ("pod", 1)


def test_scope_changed_between_plan_and_launch(ready: RunsWorld) -> None:
    """A manifest published after the plan: `needs_confirmation` again with the new plan, nothing launched."""
    old = plan_fingerprint(ready)
    write_foreign(ready.store)
    again = send(ready, LAUNCH, confirm_fingerprint=old)
    assert again.status == "needs_confirmation"
    assert again.confirmations[0].message.startswith("The plan changed since it was confirmed")
    assert (run_ids(ready), posts(ready)) == ([], 0)
    assert launch_run(ready).covered_batch_ids == (FOREIGN_BATCH_ID, FIRST_BATCH_ID)


def test_unknown_manifest_is_counted(ready: RunsWorld) -> None:
    """Another computer's manifest is in the scope (unknown), its audio in the duration and the estimate."""
    before = plan_of(ready)
    write_foreign(ready.store)
    after = plan_of(ready)
    known = {batch.batch_id: batch.known for batch in after.scope.batches}
    assert (known, after.scope.pending_duration_s) == (
        {FIRST_BATCH_ID: True, FOREIGN_BATCH_ID: False},
        SEEDED_SECONDS + FOREIGN_SECONDS,
    )
    assert (after.max_runtime_s > before.max_runtime_s, after.cost.high_usd > before.cost.high_usd) == (True, True)
