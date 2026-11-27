"""The run-activity check from the Runpod lists (plan m5 M5.9): live pods and queued pods, deploys and list lag."""

from datetime import timedelta

import pytest

from support.runs import OUTAGE, RUN_ID, RunsWorld, app_pod_name
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_DEPLOYED
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S
from transcribe_ui.runs.errors import ActivityUnknownError

# Time since the deploy of a queued pod that deployed right now.
JUST_NOW = timedelta(0)


def _kinds(world: RunsWorld) -> list[tuple[str, str]]:
    return [(source.kind, source.status) for source in world.activity().sources]


def _deployed(world: RunsWorld, pod_id: str, *, deployed_ago: timedelta = JUST_NOW) -> str:
    """Seed a `DEPLOYED` queued pod of the run, pointing at `pod_id`; return its name."""
    name = app_pod_name(RUN_ID, world.now())
    entry = world.cloud.api.queued_pods.add(name, status=QUEUED_POD_DEPLOYED)
    resolved = (world.now() - deployed_ago).isoformat().replace("+00:00", "Z")
    entry.update({"podId": pod_id, "resolvedAt": resolved})
    return name


@pytest.mark.parametrize("status", ["PROVISIONING", "RUNNING", "EXITED", "ERROR"])
def test_a_live_app_pod_is_active(runs_world: RunsWorld, status: str) -> None:
    """An app pod in any status but `TERMINATED` still exists (exited and failed pods are stragglers)."""
    runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()), status=status)
    assert _kinds(runs_world) == [("pod", status)]


def test_terminated_and_foreign_pods_are_not_active(runs_world: RunsWorld) -> None:
    """A terminated app pod the list still shows, and pods of other software, do not count."""
    pods = runs_world.cloud.api.pods
    pods.add(app_pod_name(RUN_ID, runs_world.now()), status=POD_TERMINATED)
    pods.add("someone-elses-pod", status="RUNNING")
    runs_world.cloud.api.queued_pods.add("someone-elses-queued-pod")
    assert not runs_world.activity().sources


def test_a_waiting_queued_pod_is_active(runs_world: RunsWorld) -> None:
    """A queued pod still waiting for a machine counts."""
    runs_world.cloud.api.queued_pods.add(app_pod_name(RUN_ID, runs_world.now()))
    assert _kinds(runs_world) == [("queued_pod", "QUEUED")]


@pytest.mark.parametrize("status", ["FAILED", "EXPIRED", "CANCELLED"])
def test_ended_queued_pods_are_not_active(runs_world: RunsWorld, status: str) -> None:
    """Failed, expired and cancelled queued pods the list keeps never lead to a pod."""
    runs_world.cloud.api.queued_pods.add(app_pod_name(RUN_ID, runs_world.now()), status=status)
    assert not runs_world.activity().sources


def test_deployed_queued_pod_defers_to_its_pod(runs_world: RunsWorld) -> None:
    """Once its pod is listed, the pod's own entry decides: live counts (once), terminated does not."""
    pods = runs_world.cloud.api.pods
    pod = pods.add(app_pod_name(RUN_ID, runs_world.now()))
    _deployed(runs_world, pod["id"])
    assert _kinds(runs_world) == [("pod", "RUNNING")]
    pod["status"] = POD_TERMINATED
    assert not runs_world.activity().sources


def test_deployed_pod_not_listed_yet_is_active(runs_world: RunsWorld) -> None:
    """Right after the deploy, a pod missing from the list (404 too) may be list lag: fail closed."""
    _deployed(runs_world, "pod-not-listed-yet")
    assert _kinds(runs_world) == [("queued_pod", QUEUED_POD_DEPLOYED)]
    assert runs_world.cloud.api.count("GET", "/v2/pods/pod-not-listed-yet") == 1


def test_deployed_pod_reading_terminated_is_gone(runs_world: RunsWorld) -> None:
    """A pod not listed yet that already reads `TERMINATED` by id is gone."""
    api = runs_world.cloud.api
    api.settings.list_lag = timedelta(hours=1)
    pod = api.pods.add(app_pod_name(RUN_ID, runs_world.now()), status=POD_TERMINATED)
    api.settings.list_lag = timedelta(0)
    _deployed(runs_world, pod["id"])
    assert not runs_world.activity().sources


def test_deployed_pod_gone_once_the_list_settled(runs_world: RunsWorld) -> None:
    """Long enough after the deploy, a pod missing from the list is gone; no lookup by id is needed."""
    _deployed(runs_world, "pod-gone", deployed_ago=timedelta(seconds=DEPLOY_SETTLE_S))
    assert not runs_world.activity().sources
    assert runs_world.cloud.api.count("GET", "/v2/pods/pod-gone") == 0


def test_deployed_pod_gone_past_the_name_deadline(runs_world: RunsWorld) -> None:
    """Without a deploy time, the pod name's deadline ends the wait for the list."""
    name = _deployed(runs_world, "pod-gone")
    entry = next(entry for entry in runs_world.cloud.api.queued_pods.listed() if entry["name"] == name)
    entry["resolvedAt"] = None
    assert _kinds(runs_world) == [("queued_pod", QUEUED_POD_DEPLOYED)]
    runs_world.cloud.clock.advance(timedelta(hours=4))
    assert not runs_world.activity().sources


def test_a_failed_pod_lookup_fails_closed(runs_world: RunsWorld) -> None:
    """The lookup of a deployed pod that is not listed yet fails closed too."""
    _deployed(runs_world, "pod-not-listed-yet")
    runs_world.cloud.api.fail("GET", "/v2/pods/pod-not-listed-yet", *OUTAGE)
    with pytest.raises(ActivityUnknownError):
        runs_world.activity()
