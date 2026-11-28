"""Unverified stops at the desktop deadline of `refresh_status` (plan m5 M5.12).

The deadline is the first `RUNNING` sighting + `MAX_RUNTIME_S` + `DESKTOP_DEADLINE_MARGIN_S`,
capped by the pod name deadline. The stop is verified like `cancel_worker`'s: a pod known only
from its queued pod that reads 404 right after its deploy may be lag, so the run stays followed.
"""

from datetime import timedelta
from functools import partial

import httpx
import pytest

from support.launch import launch_run, pods_of, queued_of, run_row, set_up_cloud
from support.monitor import refreshed_run
from support.runpod_api import Record
from support.runs import RunsWorld, seed_ready
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S, ERROR_STOP_UNVERIFIED, WAIT_MIN_S

# Two hours of waiting for a machine.
TWO_HOURS_S = 7200
# A second.
SECOND = timedelta(seconds=1)


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest published."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def _deploy_at(world: RunsWorld, delay: timedelta) -> None:
    """Let the run's queued pod deploy at the first request from `delay` after now."""
    world.cloud.api.queued_pods.schedule_deploy(queued_of(world)[0][0], world.now() + delay)


def test_unverified_stop_keeps_following(world: RunsWorld) -> None:
    """A terminate that does not take effect: the run stays `running` until the pod reads terminated."""
    run_id = launch_run(world).run_id
    deadline = refreshed_run(world, run_id).deadline
    world.cloud.settings.terminate_effective = False
    world.cloud.clock.moment = deadline
    refused = refreshed_run(world, run_id)
    assert (refused.stop_error, refused.run.status, len(pods_of(world))) == (ERROR_STOP_UNVERIFIED, "running", 1)
    world.cloud.settings.terminate_effective = True
    assert (refreshed_run(world, run_id).end_reason, pods_of(world)) == ("deadline", [])


def _hide_pods(records: dict[str, Record], _request: httpx.Request) -> None:
    """Drop every pod before a request is answered: a pod read or delete finds nothing (404)."""
    records.clear()


def test_unseen_pod_missing_not_gone(world: RunsWorld) -> None:
    """A pod known only from its queued pod that reads 404 right after its deploy may be lag.

    The stop at the deadline cannot be verified, so the run stays `running`; once the deploy has
    settled, the 404 counts and the run ends.
    """
    world.cloud.settings.capacity = False
    run_id = launch_run(world, wait_s=WAIT_MIN_S).run_id
    name_deadline = run_row(world, run_id)[1].name_deadline
    _deploy_at(world, name_deadline - world.now())
    world.cloud.clock.moment = name_deadline
    world.cloud.settings.list_lag = timedelta(days=1)
    world.cloud.settings.on_request = partial(_hide_pods, world.cloud.api.pods.records)
    refused = refreshed_run(world, run_id)
    payload = run_row(world, run_id)[1]
    assert (refused.stop_error, refused.run.status, payload.pod_id is not None, payload.pod_seen) == (
        ERROR_STOP_UNVERIFIED,
        "running",
        True,
        False,
    )
    world.cloud.clock.advance(timedelta(seconds=DEPLOY_SETTLE_S))
    ended = refreshed_run(world, run_id)
    assert (ended.run.status, ended.end_reason) == ("failed", "pod_gone")
