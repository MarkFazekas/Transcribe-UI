"""The labels of several installations on one bucket (architecture §3.9): `waits_for_current_run`, `removed_from_cloud`.

A `ready` batch that no live run lists waits for the current run: known from the local open run,
or from another computer's lease that the last refresh read. A `ready` batch whose manifest and
`_done.json` another computer deleted is marked removed from the cloud, no longer polled, and kept
in the history.
"""

from contextlib import closing
from datetime import timedelta

from support.batches import FIRST_BATCH_ID, SECOND_BATCH_ID
from support.buckets import create_bucket
from support.cleanup import confirmed
from support.cleanup import send as send_document
from support.credentials import TEST_BUCKET, store_request
from support.launch import launch, ok_as, pods_of, send, set_up_cloud
from support.launch_seed import seed_audio_batch
from support.monitor import end_pod, write_status
from support.runs import JSON_TYPE, RunsWorld, seed_ready
from transcribe_contracts.layout import manifest_key
from transcribe_ui.batches.responses import BatchSummary, ListBatchesResponse
from transcribe_ui.batches.run_scope import run_scope
from transcribe_ui.runs.responses import RefreshStatusResponse
from transcribe_ui.state.store import LocalState

# Item of the second batch.
SECOND_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ31"
# The label of a batch outside the current run.
WAITS = "waits_for_current_run"
# A bucket name nobody created (a typo), and another bucket of the same account.
MISSING_BUCKET = "no-such-bucket"
OTHER_BUCKET = "another-bucket"


def _summaries(world: RunsWorld) -> dict[str, BatchSummary]:
    listed = ok_as(send(world, "list_batches", include_hidden=True), ListBatchesResponse)
    return {summary.batch_id: summary for summary in listed.batches}


def _waiting(world: RunsWorld) -> list[str]:
    return sorted(batch_id for batch_id, summary in _summaries(world).items() if WAITS in summary.labels)


def _refresh(world: RunsWorld) -> RefreshStatusResponse:
    return ok_as(send(world, "refresh_status"), RefreshStatusResponse)


def _second_published(world: RunsWorld) -> None:
    seed_audio_batch(world, SECOND_BATCH_ID, SECOND_ITEM)
    world.step(SECOND_BATCH_ID)


def _other_computers_run(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """Launch on `runs_world`, publish the second batch on `r2_only_world`, which then refreshes."""
    seed_ready(runs_world)
    set_up_cloud(runs_world)
    launch(runs_world)
    _second_published(r2_only_world)
    _refresh(r2_only_world)


def test_batch_published_during_a_local_run_waits(runs_world: RunsWorld) -> None:
    """The local open run lists only its scope; the batch published since waits until the run ends."""
    seed_ready(runs_world)
    set_up_cloud(runs_world)
    run_id = launch(runs_world).run.run_id
    _second_published(runs_world)
    during = _waiting(runs_world)
    write_status(runs_world.store, run_id, "finished", runs_world.now())
    end_pod(runs_world, pods_of(runs_world)[0][0])
    _refresh(runs_world)
    assert (during, _waiting(runs_world)) == ([SECOND_BATCH_ID], [])


def test_batch_waits_for_another_computers_run(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """The R2-only computer learns of the other computer's run from its lease at the next refresh."""
    seed_ready(runs_world)
    set_up_cloud(runs_world)
    launch(runs_world)
    _second_published(r2_only_world)
    before = _waiting(r2_only_world)
    _refresh(r2_only_world)
    assert (before, _waiting(r2_only_world)) == ([], [SECOND_BATCH_ID])


def test_batch_removed_by_another_computer(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """Deleted from the cloud by another computer: marked, not polled any more, hidden on request, kept."""
    seed_ready(runs_world)
    assert send_document(r2_only_world, confirmed("everything")).status == "ok"
    marked = _refresh(runs_world)
    summary = _summaries(runs_world)[FIRST_BATCH_ID]
    assert (marked.awaiting_results, summary.stage, summary.labels, summary.actions) == (
        False,
        "done",
        ("removed_from_cloud",),
        ("hide_batch",),
    )
    assert not _refresh(runs_world).batches
    assert send(runs_world, "set_batch_hidden", batch_id=FIRST_BATCH_ID, hidden=True).status == "ok"
    assert _summaries(runs_world)[FIRST_BATCH_ID].hidden


def _use_bucket(world: RunsWorld, bucket: str) -> None:
    """Store another bucket name (a mistyped one, or another bucket of the account)."""
    assert world.leg.backend.execute_json(store_request(r2_bucket=bucket)).status == "ok"


def _labels(world: RunsWorld) -> tuple[str, ...]:
    return _summaries(world)[FIRST_BATCH_ID].labels


def test_a_missing_bucket_marks_nothing(runs_world: RunsWorld) -> None:
    """A mistyped bucket name: the refresh fails (a HEAD there finds nothing), and no batch is marked removed."""
    seed_ready(runs_world)
    _use_bucket(runs_world, MISSING_BUCKET)
    failed = send(runs_world, "refresh_status")
    _use_bucket(runs_world, TEST_BUCKET)
    assert (failed.status, _labels(runs_world), _refresh(runs_world).awaiting_results) == ("error", (), True)


def test_switching_back_to_the_bucket_restores(runs_world: RunsWorld) -> None:
    """Another existing bucket marks the batch removed; back on the right one, a refresh with the recheck follows it again."""
    seed_ready(runs_world)
    create_bucket(OTHER_BUCKET)
    _use_bucket(runs_world, OTHER_BUCKET)
    _refresh(runs_world)
    marked = _labels(runs_world)
    _use_bucket(runs_world, TEST_BUCKET)
    still = None if _refresh(runs_world).batches else _labels(runs_world)
    restored = ok_as(send(runs_world, "refresh_status", recheck_removed=True), RefreshStatusResponse)
    assert (marked, still, _labels(runs_world), restored.awaiting_results) == (
        ("removed_from_cloud",),
        ("removed_from_cloud",),
        (),
        True,
    )


def test_a_republished_manifest_is_followed_again(runs_world: RunsWorld) -> None:
    """Its manifest withdrawn (marked removed), then published again: the recheck follows the batch again."""
    seed_ready(runs_world)
    key = manifest_key(FIRST_BATCH_ID)
    body = runs_world.store.read(key)
    assert body is not None
    runs_world.store.delete(key)
    _refresh(runs_world)
    marked = _labels(runs_world)
    runs_world.store.write(key, body, JSON_TYPE)
    send(runs_world, "refresh_status", recheck_removed=True)
    assert (marked, _labels(runs_world)) == (("removed_from_cloud",), ())


def test_another_process_sees_the_same_run_scope(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """The leases the refresh read are stored: a fresh process (a CLI call) gives the same label as the GUI's backend."""
    _other_computers_run(runs_world, r2_only_world)
    state = r2_only_world.ports.state
    state.close()
    fresh = LocalState(state.paths)
    with closing(fresh), fresh.session() as session:
        scope = run_scope(session, r2_only_world.now())
    assert (scope.waits(SECOND_BATCH_ID), scope.waits(FIRST_BATCH_ID)) == (True, False)


def test_a_stored_lease_past_its_end_does_not_count(runs_world: RunsWorld, r2_only_world: RunsWorld) -> None:
    """Days later, without a refresh (a CLI call), a stored lease past its end no longer makes the batch wait."""
    _other_computers_run(runs_world, r2_only_world)
    during = _waiting(r2_only_world)
    runs_world.cloud.clock.advance(timedelta(days=3))
    assert (during, _waiting(r2_only_world)) == ([SECOND_BATCH_ID], [])
