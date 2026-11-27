"""The run-activity check (plan m5 M5.9): its three sources, what it ignores, and failing closed."""

from typing import override

import pytest

from support.credentials import TEST_BUCKET
from support.runpod_api import Failure
from support.runs import OTHER_RUN_ID, OUTAGE, RUN_ID, RunsWorld, app_pod_name, seed_heartbeat, seed_run
from support.storage import store_client
from transcribe_ui.runpod.constants import HTTP_UNAUTHORIZED
from transcribe_ui.runs.activity import check_activity
from transcribe_ui.runs.cloud import CloudAccess
from transcribe_ui.runs.errors import ActivityUnknownError
from transcribe_ui.state.errors import LockOrderError
from transcribe_ui.storage.constants import ERROR_R2_UNREACHABLE
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectInfo, R2ObjectStore

# Paths of the fake API the check reads.
PODS = "/v2/pods"
QUEUED_PODS = "/v2/queued-pods"


def _kinds(world: RunsWorld) -> list[tuple[str, str]]:
    return [(source.kind, source.status) for source in world.activity().sources]


def test_nothing_is_active_on_an_empty_cloud(runs_world: RunsWorld) -> None:
    """No local run, no app pod and no heartbeat: no source."""
    assert not runs_world.activity().sources


@pytest.mark.parametrize("run_status", ["launching", "queued", "running", "a_status_from_a_newer_app"])
def test_a_local_run_that_is_not_final_is_active(runs_world: RunsWorld, run_status: str) -> None:
    """A local run counts unless it is final; a status this version does not know counts too."""
    seed_run(runs_world, run_status)
    sources = [(source.kind, source.name, source.status) for source in runs_world.activity().sources]
    assert sources == [("local_run", RUN_ID, run_status)]


@pytest.mark.parametrize("run_status", ["finished", "failed", "cancelled", "launch_failed"])
def test_a_final_local_run_is_not_active(runs_world: RunsWorld, run_status: str) -> None:
    """Finished, failed, cancelled and never-launched runs are history."""
    seed_run(runs_world, run_status)
    assert not runs_world.activity().sources


def test_every_source_is_reported(runs_world: RunsWorld) -> None:
    """All signs are listed: local runs first, then pods and queued pods, then heartbeats."""
    seed_run(runs_world, "running")
    api = runs_world.cloud.api
    api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    api.queued_pods.add(app_pod_name(OTHER_RUN_ID, runs_world.now()))
    seed_heartbeat(runs_world, "transcribing", runs_world.now())
    sources = [(source.kind, source.run_id) for source in runs_world.activity().sources]
    assert sources == [("local_run", RUN_ID), ("pod", RUN_ID), ("queued_pod", OTHER_RUN_ID), ("heartbeat", RUN_ID)]


@pytest.mark.parametrize("path", [PODS, QUEUED_PODS])
def test_an_unreadable_runpod_list_fails_closed(runs_world: RunsWorld, path: str) -> None:
    """When a Runpod list cannot be read, the check raises `cloud_unreachable` instead of answering."""
    runs_world.cloud.api.fail("GET", path, *OUTAGE)
    with pytest.raises(ActivityUnknownError) as raised:
        runs_world.activity()
    assert (raised.value.code, raised.value.details) == (
        "cloud_unreachable",
        {"part": "runpod", "cause": "cloud_unreachable"},
    )


def test_a_rejected_key_fails_closed_naming_the_cause(runs_world: RunsWorld) -> None:
    """A 401 keeps the code `cloud_unreachable`; the cause says the key was rejected."""
    runs_world.cloud.api.fail("GET", PODS, Failure(status=HTTP_UNAUTHORIZED))
    with pytest.raises(ActivityUnknownError) as raised:
        runs_world.activity()
    assert raised.value.details == {"part": "runpod", "cause": "runpod_key_rejected"}


class UnreachableStore(R2ObjectStore):
    """The test bucket, but listing (or, with `list_works`, reading) fails as if R2 could not be reached."""

    def __init__(self, *, list_works: bool = False) -> None:
        """Use the test bucket."""
        super().__init__(store_client(), TEST_BUCKET)
        self.list_works = list_works

    @override
    def list_objects(self, prefix: str) -> tuple[ObjectInfo, ...]:
        if self.list_works:
            return super().list_objects(prefix)
        raise StorageError(ERROR_R2_UNREACHABLE, "list", "EndpointConnectionError")

    @override
    def read(self, key: str) -> bytes | None:
        raise StorageError(ERROR_R2_UNREACHABLE, "read", "EndpointConnectionError")


@pytest.mark.parametrize("list_works", [False, True])
def test_unreadable_r2_fails_closed(runs_world: RunsWorld, *, list_works: bool) -> None:
    """When R2 cannot be listed, or a heartbeat cannot be read after the listing, the check fails closed."""
    seed_heartbeat(runs_world, "transcribing", runs_world.now())
    state = runs_world.ports.state
    with state.locks.runs(), runs_world.access() as cloud:
        broken = CloudAccess(UnreachableStore(list_works=list_works), cloud.pods)
        with pytest.raises(ActivityUnknownError) as raised:
            check_activity(state, broken, runs_world.now())
    assert raised.value.details == {"part": "r2", "cause": ERROR_R2_UNREACHABLE}


def test_the_check_needs_the_runs_lock(runs_world: RunsWorld) -> None:
    """Checking without the runs lock is a programming error (a launch could start meanwhile)."""
    with runs_world.access() as cloud, pytest.raises(LockOrderError):
        check_activity(runs_world.ports.state, cloud, runs_world.now())
