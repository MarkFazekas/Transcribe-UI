"""The desktop deadline of `refresh_status` (plan m5 M5.12): termination, late deploys, verification.

The deadline is the first `RUNNING` sighting + `MAX_RUNTIME_S` + `DESKTOP_DEADLINE_MARGIN_S`,
capped by the pod name deadline. The stop is verified like `cancel_worker`'s: a pod known only
from its queued pod that reads 404 right after its deploy may be lag, so the run stays followed.
"""

from datetime import timedelta

import pytest

from support.launch import launch_run, pods_of, queued_of, run_row, set_up_cloud
from support.monitor import refresh, refreshed_run
from support.runs import RunsWorld, seed_uploaded
from transcribe_ui.runs.constants import DESKTOP_DEADLINE_MARGIN_S

# Two hours of waiting for a machine.
TWO_HOURS_S = 7200
# A second.
SECOND = timedelta(seconds=1)
# When a late pod deploys, and when the app (closed meanwhile) first sees it, after the launch (s).
LATE_DEPLOY_S = 6600
LATE_SIGHTING_S = 9000
# When a pod deploys that the app sees at once, well before the queue's 2 h expire (s).
EARLY_DEPLOY_S = 6000


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held (the launch publishes it)."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _deploy_at(world: RunsWorld, delay: timedelta) -> None:
    """Let the run's queued pod deploy at the first request from `delay` after now."""
    world.cloud.api.queued_pods.schedule_deploy(queued_of(world)[0][0], world.now() + delay)


def test_deadline_termination(world: RunsWorld) -> None:
    """Up to the deadline the pod runs; at it the pod is terminated (verified) and the run fails."""
    run_id = launch_run(world).run_id
    deadline = refresh(world).runs[0].deadline
    world.cloud.clock.moment = deadline - SECOND
    before = refreshed_run(world, run_id)
    assert (before.run.status, len(pods_of(world))) == ("running", 1)
    world.cloud.clock.moment = deadline
    stopped = refreshed_run(world, run_id)
    assert (stopped.run.status, stopped.end_reason, stopped.ended_at, pods_of(world)) == (
        "failed",
        "deadline",
        deadline,
        [],
    )


def test_late_deploy_counts_from_its_sighting(world: RunsWorld) -> None:
    """A pod deployed late gets its full runtime from its first `RUNNING` sighting."""
    world.cloud.settings.capacity = False
    run_id = launch_run(world, wait_s=TWO_HOURS_S).run_id
    _deploy_at(world, timedelta(seconds=EARLY_DEPLOY_S))
    world.cloud.clock.advance(timedelta(seconds=EARLY_DEPLOY_S))
    seen = refreshed_run(world, run_id)
    payload = run_row(world, run_id)[1]
    expected = world.now() + timedelta(seconds=payload.max_runtime_s + DESKTOP_DEADLINE_MARGIN_S)
    assert (seen.first_running_at, seen.deadline) == (world.now(), expected)
    assert expected < payload.name_deadline
    world.cloud.clock.moment = expected - SECOND
    assert refreshed_run(world, run_id).run.status == "running"
    world.cloud.clock.moment = expected
    assert (refreshed_run(world, run_id).end_reason, pods_of(world)) == ("deadline", [])


def test_late_sighting_capped_by_the_name_deadline(world: RunsWorld) -> None:
    """A pod first seen running long after its deploy (the app was closed) stops at the name deadline."""
    world.cloud.settings.capacity = False
    run_id = launch_run(world, wait_s=TWO_HOURS_S).run_id
    _deploy_at(world, timedelta(seconds=LATE_DEPLOY_S))
    world.cloud.clock.advance(timedelta(seconds=LATE_SIGHTING_S))
    seen = refreshed_run(world, run_id)
    name_deadline = run_row(world, run_id)[1].name_deadline
    assert (seen.run.status, seen.deadline) == ("running", name_deadline)
    world.cloud.clock.moment = name_deadline
    assert (refreshed_run(world, run_id).end_reason, pods_of(world)) == ("deadline", [])
