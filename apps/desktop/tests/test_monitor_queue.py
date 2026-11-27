"""Queued pods in `refresh_status` (plan m5 M5.12, review fixes): vanished ones, missing deploy times, deadline stops.

A queued pod Runpod no longer knows ends its run only after the settle time and two empty
lookups; a pod known only from its queued pod settles from when the run recorded it when the
queued pod gives no `resolvedAt`; a run that has only a queued-pod id is stopped at its deadline
like `cancel_worker` stops it.
"""

from datetime import datetime, timedelta
from functools import partial
from typing import Any

import httpx
import pytest

from support.launch import QUEUED_PODS, launch_run, pods_of, queued_of, run_row, set_up_cloud
from support.monitor import refresh, refreshed_run
from support.runpod_api import FakeRunpodApi, iso
from support.runs import RunsWorld, seed_uploaded
from transcribe_ui.runs.constants import (
    DEPLOY_SETTLE_S,
    ERROR_STOP_UNVERIFIED,
    LAUNCH_LOOKUP_GAP_S,
    WAIT_MIN_S,
)

# Two hours of waiting for a machine.
TWO_HOURS_S = 7200
# Time after the queue's `expiresAt` before a vanished queued pod can count as ended.
SETTLE = timedelta(seconds=DEPLOY_SETTLE_S)
# A second.
SECOND = timedelta(seconds=1)
# Id of a pod a queued pod names but Runpod does not show.
HIDDEN_POD = "pod-hidden"


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up, the seeded batch uploaded, no capacity: the launch queues."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    runs_world.cloud.settings.capacity = False
    return runs_world


def _queued_body(world: RunsWorld) -> dict[str, Any]:
    """Return the stored body of the run's queued pod (tests change it as Runpod would)."""
    records = world.cloud.api.queued_pods.records
    body: dict[str, Any] = records[queued_of(world)[0][0]].body
    return body


def test_vanished_queued_pod_needs_two_lookups(world: RunsWorld) -> None:
    """A queued pod Runpod no longer knows: kept before expiry + settle, then failed after two empty lookups."""
    run_id = launch_run(world, wait_s=WAIT_MIN_S).run_id
    settled_from = run_row(world, run_id)[1].queue_expires_at + SETTLE
    queued_pods = world.cloud.api.queued_pods
    queued_pods.records.clear()
    world.cloud.clock.moment = settled_from - SECOND
    early = refresh(world).runs[0]
    assert (early.run.status, run_row(world, run_id)[1].first_empty_lookup_at) == ("queued", None)
    world.cloud.clock.moment = settled_from
    first = refresh(world).runs[0]
    assert (first.run.status, run_row(world, run_id)[1].first_empty_lookup_at) == ("queued", settled_from)
    world.cloud.clock.advance(timedelta(seconds=LAUNCH_LOOKUP_GAP_S) - SECOND)
    assert refreshed_run(world, run_id).run.status == "queued"
    world.cloud.clock.advance(SECOND)
    ended = refreshed_run(world, run_id)
    assert (ended.run.status, ended.end_reason) == ("failed", "queue_ended")


def test_unseen_pod_without_deploy_time_settles(world: RunsWorld) -> None:
    """A deployed queued pod without `resolvedAt` whose pod reads 404: gone once settled from when it was recorded."""
    run_id = launch_run(world, wait_s=TWO_HOURS_S).run_id
    _queued_body(world).update({"status": "DEPLOYED", "podId": HIDDEN_POD, "resolvedAt": None})
    followed = refresh(world).runs[0]
    payload = run_row(world, run_id)[1]
    assert (followed.run.status, payload.pod_id, payload.pod_seen, payload.pod_deployed_at) == (
        "running",
        HIDDEN_POD,
        False,
        None,
    )
    assert payload.pod_recorded_at == world.now()
    world.cloud.clock.advance(SETTLE - SECOND)
    assert refreshed_run(world, run_id).run.status == "running"
    world.cloud.clock.advance(SECOND)
    ended = refreshed_run(world, run_id)
    assert (ended.run.status, ended.end_reason) == ("failed", "pod_gone")


def test_deadline_stop_without_its_pod_id(world: RunsWorld) -> None:
    """At the deadline the queued pod reads `DEPLOYED` without its pod: the stop cannot be verified."""
    run_id = launch_run(world, wait_s=TWO_HOURS_S).run_id
    _queued_body(world).update({"status": "DEPLOYED", "podId": None, "resolvedAt": iso(world.now())})
    world.cloud.clock.moment = run_row(world, run_id)[1].name_deadline
    refused = refreshed_run(world, run_id)
    assert (refused.stop_error, refused.run.status, refused.ended_at) == (ERROR_STOP_UNVERIFIED, "running", None)


def _deploy_on_cancel(api: FakeRunpodApi, queued_pod_id: str, now: datetime, request: httpx.Request) -> None:
    """Let the queued pod deploy just as its cancel arrives."""
    if request.method == "DELETE" and request.url.path == f"{QUEUED_PODS}/{queued_pod_id}":
        api.queued_pods.schedule_deploy(queued_pod_id, now)


def test_deadline_stop_with_a_deploy_during_the_cancel(world: RunsWorld) -> None:
    """The queued pod deploys while it is cancelled at the deadline: its pod is recorded, terminated, verified."""
    run_id = launch_run(world, wait_s=TWO_HOURS_S).run_id
    queued_pod_id = queued_of(world)[0][0]
    deadline = run_row(world, run_id)[1].name_deadline
    _queued_body(world)["queue"]["expiresAt"] = iso(deadline + timedelta(days=1))
    world.cloud.settings.keep_terminal = True
    world.cloud.clock.moment = deadline
    world.cloud.settings.on_request = partial(_deploy_on_cancel, world.cloud.api, queued_pod_id, deadline)
    stopped = refreshed_run(world, run_id)
    payload = run_row(world, run_id)[1]
    assert (stopped.run.status, stopped.end_reason, payload.pod_id is not None) == ("failed", "deadline", True)
    assert pods_of(world) == [(payload.pod_id, "TERMINATED")]
