"""The run lease `runs/<run_id>/launch.json` (A21, architecture §3.9 "Run scope"): its lifecycle and its bounds.

The launch writes it after the run row and before the pod request; every request that makes the
run final with its pod gone (or never created) deletes it, best effort. A lease counts until its
deadline and no longer once the run's `status.json` is final; its R2 write time bounds it.
"""

from datetime import timedelta
from functools import partial

import httpx
import pytest

from support.batches import FIRST_BATCH_ID
from support.launch import (
    CANCEL,
    LAUNCH,
    PODS,
    POST,
    error_code,
    launch,
    launch_run,
    plan_fingerprint,
    pods_of,
    posts,
    run_ids,
    run_row,
    send,
    set_up_cloud,
)
from support.monitor import end_pod, refresh, refreshed_run, write_status
from support.runpod_api import Failure
from support.runs import JSON_TYPE, OTHER_RUN_ID, RUN_ID, RunsWorld, seed_ready
from transcribe_contracts.classes import RunLease
from transcribe_contracts.layout import run_lease_key
from transcribe_contracts.reading import read_run_lease
from transcribe_ui.runs.constants import ERROR_STOP_UNVERIFIED, LEASE_MAX_SPAN_S, WAIT_MIN_S
from transcribe_ui.runs.leases import delete_lease, live_leases, write_lease
from transcribe_ui.storage.constants import ERROR_R2_UNREACHABLE
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore

# A lease's span plus a minute of slack: the test clock starts at the real time, before moto stamps the object.
LONG_AFTER = timedelta(seconds=LEASE_MAX_SPAN_S + 60)
# A second.
SECOND = timedelta(seconds=1)
# Where the ending of a run deletes leases (patched to watch or fail it).
RELEASE_DELETE = "transcribe_ui.runs.lease_release.delete_lease"
# The batch ids another computer's lease lists.
OTHER_BATCHES = ("batch_20260101_090000",)


@pytest.fixture
def world(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded (the launch publishes its manifest)."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def _recorded_delete(calls: list[str], store: ObjectStore, run_id: str) -> None:
    calls.append(run_id)
    delete_lease(store, run_id)


def _failing_r2(*_args: object) -> None:
    raise StorageError(ERROR_R2_UNREACHABLE, "lease", "ConnectionError")


def _note_post(world: RunsWorld, seen: list[tuple[str, RunLease | None]], request: httpx.Request) -> None:
    """At the pod request, note the run's status and its lease as they are then."""
    if request.method == POST and request.url.path == PODS:
        run_id = run_ids(world)[0]
        seen.append((run_row(world, run_id)[0], _lease(world, run_id)))


@pytest.fixture
def deleted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the run ids whose lease the ending of a run deletes (the delete still happens)."""
    calls: list[str] = []
    monkeypatch.setattr(RELEASE_DELETE, partial(_recorded_delete, calls))
    return calls


def _lease(world: RunsWorld, run_id: str) -> RunLease | None:
    key = run_lease_key(run_id)
    body = world.store.read(key)
    return None if body is None else read_run_lease(key, body)


def _cancel(world: RunsWorld, run_id: str) -> str:
    """Send a confirmed cancel; return the response's status or error code."""
    response = send(world, CANCEL, run_id=run_id, confirm=True)
    return "ok" if response.status == "ok" else error_code(response)


def test_lease_written_before_the_pod_request(world: RunsWorld) -> None:
    """At the pod request the run row is committed and the lease lists the covered batches until the name deadline."""
    seen: list[tuple[str, RunLease | None]] = []
    world.cloud.settings.on_request = partial(_note_post, world, seen)
    run = launch_run(world)
    lease = _lease(world, run.run_id)
    assert lease is not None
    assert (lease.batch_ids, lease.deadline_at, run.covered_batch_ids) == (
        (FIRST_BATCH_ID,),
        run.name_deadline,
        (FIRST_BATCH_ID,),
    )
    assert seen == [("launching", lease)]


def test_failed_lease_write_creates_nothing(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """R2 refuses the lease: the run is `launch_failed`, no pod is requested, the launch reports the R2 error."""
    monkeypatch.setattr("transcribe_ui.runs.starting.write_lease", _failing_r2)
    fingerprint = plan_fingerprint(world)
    refused = send(world, LAUNCH, confirm_fingerprint=fingerprint)
    run_id = run_ids(world)[0]
    status, payload = run_row(world, run_id)
    assert (error_code(refused), status, payload.end_reason, posts(world)) == (
        ERROR_R2_UNREACHABLE,
        "launch_failed",
        "launch_failed",
        0,
    )


def test_refused_pod_request_deletes_the_lease(world: RunsWorld, deleted: list[str]) -> None:
    """Runpod refuses the pod: the run is `launch_failed` and its lease goes."""
    world.cloud.api.fail(POST, PODS, Failure(status=httpx.codes.BAD_REQUEST))
    refused = send(world, LAUNCH, confirm_fingerprint=plan_fingerprint(world))
    run_id = run_ids(world)[0]
    assert (error_code(refused), deleted, _lease(world, run_id)) == ("launch_refused", [run_id], None)


def test_cancel_deletes_the_lease_relaunch_works(world: RunsWorld, deleted: list[str]) -> None:
    """A run cancelled while queued never wrote `status.json`; its lease goes, so Start works at once."""
    world.cloud.settings.capacity = False
    run_id = launch_run(world).run_id
    assert _cancel(world, run_id) == "ok"
    assert (deleted, _lease(world, run_id)) == ([run_id], None)
    world.cloud.settings.capacity = True
    relaunched = launch(world)
    assert (relaunched.outcome, relaunched.run.run_id != run_id) == ("pod", True)


def test_unverified_cancel_keeps_the_lease(world: RunsWorld, deleted: list[str]) -> None:
    """A terminate that does not take effect leaves the run open, and its lease with it."""
    run_id = launch_run(world).run_id
    world.cloud.settings.terminate_effective = False
    assert (_cancel(world, run_id), deleted) == (ERROR_STOP_UNVERIFIED, [])
    assert _lease(world, run_id) is not None


def test_failed_lease_delete_does_not_fail_the_cancel(world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """R2 fails on the lease delete: the verified cancel stands, the lease stays for collection."""
    monkeypatch.setattr(RELEASE_DELETE, _failing_r2)
    run_id = launch_run(world).run_id
    assert _cancel(world, run_id) == "ok"
    assert (run_row(world, run_id)[0], _lease(world, run_id) is not None) == ("cancelled", True)


def test_deadline_stop_deletes_the_lease(world: RunsWorld, deleted: list[str]) -> None:
    """The desktop deadline stop makes the run final: its lease is deleted (before collection)."""
    run_id = launch_run(world).run_id
    world.cloud.clock.moment = refresh(world).runs[0].deadline
    assert (refreshed_run(world, run_id).end_reason, deleted, _lease(world, run_id)) == ("deadline", [run_id], None)


def test_unverified_deadline_stop_keeps_the_lease(world: RunsWorld, deleted: list[str]) -> None:
    """A deadline stop that does not show keeps the run followed and its lease."""
    run_id = launch_run(world).run_id
    world.cloud.clock.moment = refresh(world).runs[0].deadline
    world.cloud.settings.terminate_effective = False
    stopped = refreshed_run(world, run_id)
    assert (stopped.run.status, stopped.stop_error, deleted) == ("running", ERROR_STOP_UNVERIFIED, [])
    assert _lease(world, run_id) is not None


def test_pod_gone_deletes_the_lease(world: RunsWorld, deleted: list[str]) -> None:
    """The worker reported its end and its pod is gone: the run ends and its lease goes."""
    run_id = launch_run(world).run_id
    write_status(world.store, run_id, "finished", world.now())
    end_pod(world, pods_of(world)[0][0])
    assert (refreshed_run(world, run_id).end_reason, deleted) == ("worker_finished", [run_id])


def test_expired_queue_deletes_the_lease(world: RunsWorld, deleted: list[str]) -> None:
    """The queued pod expires without a pod (refresh step 1): the run fails and its lease goes."""
    world.cloud.settings.capacity = False
    run_id = launch_run(world, wait_s=WAIT_MIN_S).run_id
    world.cloud.clock.advance(timedelta(seconds=WAIT_MIN_S) + SECOND)
    ended = refreshed_run(world, run_id)
    assert (ended.run.status, ended.end_reason, deleted) == ("failed", "queue_ended", [run_id])


def _other_lease(world: RunsWorld, deadline_in: timedelta) -> None:
    write_lease(world.store, OTHER_RUN_ID, OTHER_BATCHES, world.now() + deadline_in)


def test_live_lease_until_its_deadline_or_final_status(runs_world: RunsWorld) -> None:
    """Another computer's lease counts until its deadline, and no longer once its run reported its end."""
    _other_lease(runs_world, timedelta(hours=1))
    live = live_leases(runs_world.store, runs_world.now())
    assert [(lease.run_id, lease.batch_ids) for lease in live] == [(OTHER_RUN_ID, frozenset(OTHER_BATCHES))]
    assert live[0].lists(OTHER_BATCHES[0])
    assert not live[0].lists(FIRST_BATCH_ID)
    assert not live_leases(runs_world.store, runs_world.now() + timedelta(hours=1))
    write_status(runs_world.store, OTHER_RUN_ID, "finished", runs_world.now())
    assert not live_leases(runs_world.store, runs_world.now())


def test_unreadable_lease_lists_every_batch(runs_world: RunsWorld) -> None:
    """A lease that cannot be read counts for every batch, but only within its write time + the span."""
    runs_world.store.write(run_lease_key(RUN_ID), b"{broken", JSON_TYPE)
    live = live_leases(runs_world.store, runs_world.now())
    assert [(lease.run_id, lease.batch_ids, lease.lists(FIRST_BATCH_ID)) for lease in live] == [(RUN_ID, None, True)]
    assert not live_leases(runs_world.store, runs_world.now() + LONG_AFTER)


def test_far_deadline_is_capped_at_the_bound(runs_world: RunsWorld) -> None:
    """A deadline far ahead (a broken writer) counts no longer than the write time + the span."""
    _other_lease(runs_world, timedelta(weeks=4))
    assert live_leases(runs_world.store, runs_world.now())
    assert not live_leases(runs_world.store, runs_world.now() + LONG_AFTER)


def test_lease_counts_for_another_launch(runs_world: RunsWorld) -> None:
    """The launch's activity check names another computer's live lease."""
    _other_lease(runs_world, timedelta(hours=1))
    sources = [(source.kind, source.status, source.run_id) for source in runs_world.activity().sources]
    assert sources == [("lease", "live", OTHER_RUN_ID)]
