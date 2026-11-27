"""`launch_worker` interrupted around its pod request, and launches that must not start (plan m5 M5.11)."""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from functools import partial

import httpx
import pytest

from support.batches import FIRST_BATCH_ID
from support.launch import (
    LAUNCH,
    PODS,
    POST,
    document,
    error_code,
    launch,
    launch_confirmed,
    plan_fingerprint,
    pods_of,
    posts,
    run_ids,
    run_row,
    run_status,
    send,
    set_up_cloud,
)
from support.runpod_api import Failure, Record
from support.runs import RunsWorld, seed_uploaded
from transcribe_ui.api.constants import ERROR_CANCELLED, ERROR_INTERNAL
from transcribe_ui.batches.payloads import PendingWithdrawal
from transcribe_ui.runpod.constants import QUEUED_POD_CANCELLED
from transcribe_ui.runs.constants import ERROR_RUN_ACTIVE, LAUNCH_LOOKUP_GAP_S, LAUNCH_SETTLE_S
from transcribe_ui.state.constants import ERROR_BATCH_BUSY
from transcribe_ui.state.models import BatchRow

# Settle time after the pod request, and the gap between the two empty lookups.
SETTLE = timedelta(seconds=LAUNCH_SETTLE_S)
GAP = timedelta(seconds=LAUNCH_LOOKUP_GAP_S)
# A digest no manifest has (the pending withdrawal finds no leftover to delete).
NO_DIGEST = hashlib.sha256(b"no manifest").hexdigest()


class CancelWhen:
    """A cancel token that reads cancelled once its condition holds."""

    def __init__(self, condition: Callable[[], bool]) -> None:
        """Cancel once `condition` returns True."""
        self._condition = condition

    def is_cancelled(self) -> bool:
        """Return whether the condition holds."""
        return self._condition()


@pytest.fixture
def ready(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the seeded batch uploaded, its manifest held."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    return runs_world


def _launch_with(world: RunsWorld, cancel: CancelWhen) -> str:
    fingerprint = plan_fingerprint(world)
    request = document(LAUNCH, confirm_fingerprint=fingerprint)
    response = world.leg.backend.execute_json(request, cancel=cancel)
    if response.status == "error":
        return response.error.code
    return response.status


def _crash_at_post(request: httpx.Request) -> None:
    """Stop the app as the pod request is about to be answered (nothing is created)."""
    if (request.method, request.url.path) == (POST, PODS):
        msg = "the app stopped here"
        raise RuntimeError(msg)


def _cancel_queued_at_lookup(records: dict[str, Record], request: httpx.Request) -> None:
    """Mark every queued pod cancelled when the pod list is read (the lookup after the POST)."""
    if request.method == "GET" and request.url.path == PODS:
        for record in records.values():
            record.body["status"] = QUEUED_POD_CANCELLED


def _reconcile_through_settling(world: RunsWorld) -> None:
    for step in (SETTLE, GAP):
        world.cloud.clock.advance(step)
        assert send(world, "reconcile_cloud").status == "ok"


def test_crash_between_the_run_row_and_the_pod_request(ready: RunsWorld) -> None:
    """A crash as the POST is sent leaves a `launching` run: active, then `launch_failed` after settling."""
    ready.cloud.settings.on_request = _crash_at_post
    assert error_code(launch_confirmed(ready)) == ERROR_INTERNAL
    ready.cloud.settings.on_request = None
    run_id = run_ids(ready)[0]
    assert (run_status(ready, run_id), pods_of(ready)) == ("launching", [])
    assert error_code(launch_confirmed(ready)) == ERROR_RUN_ACTIVE
    _reconcile_through_settling(ready)
    status, payload = run_row(ready, run_id)
    assert (status, payload.end_reason, payload.ended_at) == ("launch_failed", "launch_failed", ready.now())


def test_cancel_before_pod_request_is_launch_failed(ready: RunsWorld) -> None:
    """Cancelled once the run is recorded: no POST is sent and the run is `launch_failed`."""
    code = _launch_with(ready, CancelWhen(lambda: bool(run_ids(ready))))
    assert (code, posts(ready), run_status(ready, run_ids(ready)[0])) == (ERROR_CANCELLED, 0, "launch_failed")
    assert run_row(ready, run_ids(ready)[0])[1].end_reason == "launch_failed"


def test_cancel_during_the_lookup_stays_unconfirmed(ready: RunsWorld) -> None:
    """A cancel after a lost answer does not hide it: the run stays `launching`, answered as unconfirmed."""
    ready.cloud.api.fail(POST, PODS, Failure(lost_response=True))
    outcome = _launch_with(ready, CancelWhen(lambda: posts(ready) > 0))
    assert (outcome, run_status(ready, run_ids(ready)[0]), posts(ready)) == ("ok", "launching", 1)


def test_queued_pod_found_ended_after_a_lost_answer(ready: RunsWorld) -> None:
    """The lookup after a lost answer finds the queued pod cancelled already: a final outcome, not unconfirmed."""
    ready.cloud.settings.capacity = False
    ready.cloud.api.fail(POST, PODS, Failure(lost_response=True))
    ready.cloud.settings.on_request = partial(_cancel_queued_at_lookup, ready.cloud.api.queued_pods.records)
    launched = launch(ready)
    assert (launched.outcome, launched.adopted, launched.run.status) == ("ended", True, "cancelled")
    payload = run_row(ready, launched.run.run_id)[1]
    assert (payload.end_reason, payload.ended_at) == ("queue_ended", ready.now())


def test_busy_pending_withdrawal_refuses_the_launch(ready: RunsWorld) -> None:
    """A leftover manifest's withdrawal left pending (its batch busy): `batch_busy`, no run, no pod."""
    pending = PendingWithdrawal(manifest_sha256=NO_DIGEST, item_ids=()).model_dump(mode="json")
    with ready.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        row.pending_withdrawal = pending
    with ready.ports.state.locks.batch(FIRST_BATCH_ID):
        refused = launch_confirmed(ready)
    assert (error_code(refused), run_ids(ready), posts(ready)) == (ERROR_BATCH_BUSY, [], 0)
    assert launch(ready).run.status == "running"


def test_test_mode_needs_its_own_confirmation(ready: RunsWorld) -> None:
    """A plan confirmed without test mode does not start a test-mode run."""
    fingerprint = plan_fingerprint(ready)
    asked = send(ready, LAUNCH, confirm_fingerprint=fingerprint, test_mode=True)
    assert (asked.status, run_ids(ready), posts(ready)) == ("needs_confirmation", [], 0)
