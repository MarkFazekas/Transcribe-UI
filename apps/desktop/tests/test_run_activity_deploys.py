"""The run-activity check for deployed queued pods (plan m5 M5.9): the pod list, lookups, list lag, deadlines."""

from datetime import timedelta

import pytest

from support.runs import OUTAGE, RUN_ID, RunsWorld, app_pod_name
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_DEPLOYED
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S
from transcribe_ui.runs.errors import ActivityUnknownError

# What a deployed queued pod that still counts adds to the activity.
DEPLOYED_SOURCE = ("queued_pod", QUEUED_POD_DEPLOYED)
# Time since the deploy of a queued pod that deployed right now.
JUST_NOW = timedelta(0)
# Past the name deadline of the test pods (`NAME_DEADLINE` is 3 hours).
PAST_DEADLINE = timedelta(hours=4)
# Long enough for a missing pod to count as gone, but before the name deadline.
SETTLED = timedelta(seconds=DEPLOY_SETTLE_S)
# Id of a deployed pod Runpod does not know (404).
GONE = "pod-gone"
# Lookup path of that pod.
GONE_PATH = f"/v2/pods/{GONE}"
# Name of a pod that is not the app's.
FOREIGN_NAME = "renamed-by-hand"


def _kinds(world: RunsWorld) -> list[tuple[str, str]]:
    return [(source.kind, source.status) for source in world.activity().sources]


def _deployed(world: RunsWorld, pod_id: str | None, *, deployed_ago: timedelta = JUST_NOW) -> str:
    """Seed a `DEPLOYED` queued pod of the run, pointing at `pod_id` (Runpod's clock: now); return its name."""
    name = app_pod_name(RUN_ID, world.now())
    entry = world.cloud.api.queued_pods.add(name, status=QUEUED_POD_DEPLOYED)
    resolved = (world.now() - deployed_ago).isoformat().replace("+00:00", "Z")
    entry.update({"podId": pod_id, "resolvedAt": resolved})
    return name


def _unlisted_pod(world: RunsWorld, pod_status: str = "RUNNING") -> str:
    """Seed an app pod the list leaves out (list lag); return its id."""
    api = world.cloud.api
    api.settings.list_lag = timedelta(days=1)
    pod = api.pods.add(app_pod_name(RUN_ID, world.now()), status=pod_status)
    api.settings.list_lag = timedelta(0)
    return str(pod["id"])


def test_deployed_queued_pod_defers_to_its_pod(runs_world: RunsWorld) -> None:
    """Once its pod is listed, the pod's own entry decides: live counts (once), terminated does not."""
    pod = runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    _deployed(runs_world, pod["id"])
    assert _kinds(runs_world) == [("pod", "RUNNING")]
    pod["status"] = POD_TERMINATED
    assert not runs_world.activity().sources


def test_deployed_pod_listed_under_another_name(runs_world: RunsWorld) -> None:
    """A deployed pod renamed by hand is found by id in the full list: live counts through the queued pod."""
    pod = runs_world.cloud.api.pods.add(FOREIGN_NAME)
    _deployed(runs_world, pod["id"])
    assert _kinds(runs_world) == [DEPLOYED_SOURCE]
    pod["status"] = POD_TERMINATED
    assert not runs_world.activity().sources


def test_deployed_pod_not_listed_yet_is_active(runs_world: RunsWorld) -> None:
    """Right after the deploy, a pod missing from the list (404 too) may be list lag: fail closed."""
    _deployed(runs_world, GONE)
    assert _kinds(runs_world) == [DEPLOYED_SOURCE]
    assert runs_world.cloud.api.count("GET", GONE_PATH) == 1


def test_deployed_pod_reading_terminated_is_gone(runs_world: RunsWorld) -> None:
    """A pod not listed yet that already reads `TERMINATED` by id is gone."""
    _deployed(runs_world, _unlisted_pod(runs_world, POD_TERMINATED))
    assert not runs_world.activity().sources


def test_unlisted_live_pod_counts_with_a_skewed_clock(runs_world: RunsWorld) -> None:
    """A live pod the list leaves out counts even when this computer's clock says the list has settled."""
    _deployed(runs_world, _unlisted_pod(runs_world))
    runs_world.cloud.clock.advance(SETTLED * 2)
    assert _kinds(runs_world) == [DEPLOYED_SOURCE]


def test_deployed_pod_gone_once_the_list_settled(runs_world: RunsWorld) -> None:
    """Long enough after the deploy, a 404 for the pod means it is gone."""
    _deployed(runs_world, GONE, deployed_ago=SETTLED)
    assert not runs_world.activity().sources
    assert runs_world.cloud.api.count("GET", GONE_PATH) == 1


def test_deployed_pod_gone_past_the_name_deadline(runs_world: RunsWorld) -> None:
    """Without a deploy time a 404 counts, until the name deadline ends it (without a lookup)."""
    name = _deployed(runs_world, GONE)
    entry = next(entry for entry in runs_world.cloud.api.queued_pods.listed() if entry["name"] == name)
    entry["resolvedAt"] = None
    assert _kinds(runs_world) == [DEPLOYED_SOURCE]
    runs_world.cloud.clock.advance(PAST_DEADLINE)
    assert not runs_world.activity().sources
    assert runs_world.cloud.api.count("GET", GONE_PATH) == 1


def test_deployed_without_pod_id_counts_until_deadline(runs_world: RunsWorld) -> None:
    """A deployed queued pod that does not name its pod counts until its name deadline."""
    _deployed(runs_world, None, deployed_ago=SETTLED)
    assert _kinds(runs_world) == [DEPLOYED_SOURCE]
    runs_world.cloud.clock.advance(PAST_DEADLINE)
    assert not runs_world.activity().sources


def test_a_failed_pod_lookup_fails_closed(runs_world: RunsWorld) -> None:
    """The lookup of a deployed pod that is not listed fails closed too."""
    _deployed(runs_world, GONE)
    runs_world.cloud.api.fail("GET", GONE_PATH, *OUTAGE)
    with pytest.raises(ActivityUnknownError):
        runs_world.activity()
