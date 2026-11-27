"""The run-activity check from the Runpod lists (plan m5 M5.9): live pods and queued pods, deploys and list lag."""

import pytest

from support.runs import RUN_ID, RunsWorld, app_pod_name
from transcribe_ui.runpod.constants import POD_TERMINATED

# Pods listed before the app pod in the pagination test (one per page).
OTHER_PODS = 3


def _kinds(world: RunsWorld) -> list[tuple[str, str]]:
    return [(source.kind, source.status) for source in world.activity().sources]


@pytest.mark.parametrize("pod_status", ["PROVISIONING", "RUNNING", "EXITED", "ERROR"])
def test_a_live_app_pod_is_active(runs_world: RunsWorld, pod_status: str) -> None:
    """An app pod in any status but `TERMINATED` still exists (exited and failed pods are stragglers)."""
    runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()), status=pod_status)
    sources = [(source.kind, source.status, source.run_id) for source in runs_world.activity().sources]
    assert sources == [("pod", pod_status, RUN_ID)]


def test_terminated_and_foreign_pods_are_not_active(runs_world: RunsWorld) -> None:
    """A terminated app pod the list still shows, and pods of other software, do not count."""
    pods = runs_world.cloud.api.pods
    pods.add(app_pod_name(RUN_ID, runs_world.now()), status=POD_TERMINATED)
    pods.add("someone-elses-pod", status="RUNNING")
    runs_world.cloud.api.queued_pods.add("someone-elses-queued-pod")
    assert not runs_world.activity().sources


def test_a_live_pod_on_a_later_page_is_found(runs_world: RunsWorld) -> None:
    """The check follows the pod list's pages: an app pod behind other pods still counts."""
    api = runs_world.cloud.api
    api.settings.page_size = 1
    api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    for index in range(OTHER_PODS):
        api.pods.add(f"other-pod-{index}")
    assert _kinds(runs_world) == [("pod", "RUNNING")]
    assert api.count("GET", "/v2/pods") == OTHER_PODS + 1


@pytest.mark.parametrize("queued_status", ["QUEUED", "A_STATUS_FROM_THE_FUTURE"])
def test_a_waiting_queued_pod_is_active(runs_world: RunsWorld, queued_status: str) -> None:
    """A queued pod waiting for a machine counts, and so does one in a status this version does not know."""
    runs_world.cloud.api.queued_pods.add(app_pod_name(RUN_ID, runs_world.now()), status=queued_status)
    assert _kinds(runs_world) == [("queued_pod", queued_status)]


@pytest.mark.parametrize("queued_status", ["FAILED", "EXPIRED", "CANCELLED"])
def test_ended_queued_pods_are_not_active(runs_world: RunsWorld, queued_status: str) -> None:
    """Failed, expired and cancelled queued pods the list keeps never lead to a pod."""
    runs_world.cloud.api.queued_pods.add(app_pod_name(RUN_ID, runs_world.now()), status=queued_status)
    assert not runs_world.activity().sources
