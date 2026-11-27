"""`reconcile_cloud` and deployed queued pods: the activity check's rule (plan m5 M5.11).

The fake follows the documented API here: a terminated pod leaves the list and reads 404.
"""

from datetime import timedelta

from support.launch import ok_as, send
from support.runs import RUN_ID, RunsWorld, app_pod_name
from transcribe_ui.runpod.constants import POD_TERMINATED, QUEUED_POD_DEPLOYED
from transcribe_ui.runs.constants import DEPLOY_SETTLE_S
from transcribe_ui.runs.responses import ReconcileCloudResponse

# Long enough after a deploy for a missing pod to count as gone (before the name deadline).
SETTLED = timedelta(seconds=DEPLOY_SETTLE_S)
# Past the name deadline of the test pods (3 hours).
PAST_DEADLINE = timedelta(hours=4)
# How long a pod stays out of the list in the lag test.
LIST_LAG = timedelta(days=1)


def _kinds(world: RunsWorld) -> list[tuple[str, str]]:
    reconciled = ok_as(send(world, "reconcile_cloud"), ReconcileCloudResponse)
    return [(entry.kind, entry.status) for entry in reconciled.entries]


def _deployed(world: RunsWorld, pod_id: str) -> None:
    """Seed a `DEPLOYED` queued pod of the run, deployed now, pointing at `pod_id`."""
    entry = world.cloud.api.queued_pods.add(app_pod_name(RUN_ID, world.now()), status=QUEUED_POD_DEPLOYED)
    entry.update({"podId": pod_id, "resolvedAt": world.now().isoformat().replace("+00:00", "Z")})


def test_terminated_deployed_pod_gone_once_settled(runs_world: RunsWorld) -> None:
    """Its pod terminated (gone, 404): the queued pod shows until the deploy has settled, then not."""
    _deployed(runs_world, "pod-terminated")
    assert _kinds(runs_world) == [("queued_pod", QUEUED_POD_DEPLOYED)]
    runs_world.cloud.clock.advance(SETTLED)
    assert _kinds(runs_world) == []


def test_unlisted_pod_reading_terminated_is_gone(runs_world: RunsWorld) -> None:
    """A pod the list leaves out but that reads `TERMINATED` by id ends its queued pod."""
    api = runs_world.cloud.api
    api.settings.list_lag = LIST_LAG
    pod = api.pods.add(app_pod_name(RUN_ID, runs_world.now()), status=POD_TERMINATED)
    api.settings.list_lag = timedelta(0)
    _deployed(runs_world, str(pod["id"]))
    assert _kinds(runs_world) == []


def test_deployed_pod_listed_as_itself_is_one_entry(runs_world: RunsWorld) -> None:
    """A deployed queued pod whose app pod is listed shows as that pod only; past the deadline it is overdue."""
    pod = runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    _deployed(runs_world, str(pod["id"]))
    assert _kinds(runs_world) == [("pod", "RUNNING")]
    runs_world.cloud.clock.advance(PAST_DEADLINE)
    assert _kinds(runs_world) == [("pod", "RUNNING")]
