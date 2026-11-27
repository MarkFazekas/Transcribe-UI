"""A withdrawal that fails after its change was saved, and who finishes it (plan m5 M5.9, architecture §3.3)."""

import pytest

from support.batches import FILM, FIRST_BATCH_ID, PODCAST
from support.runs import HELD, MANIFEST, RUN_ID, RunsWorld, app_pod_name, batch_state, read_back
from support.withdrawal import (
    all_present,
    exclude_film,
    fail_deleting,
    none_present,
    pending_of,
    rereview,
    result_keys,
    seed_leftover,
)
from transcribe_contracts.layout import done_key, result_json_key
from transcribe_contracts.reading import read_manifest

# Which delete of the withdrawal fails: the film's result JSON, `_done.json` or the manifest.
FAILING = ("result", "done", "manifest")


class Crash(Exception):
    """A simulated crash of the process."""


def _crash(*_args: object, **_kwargs: object) -> None:
    raise Crash


def _failing_key(failing: str, film_id: str) -> str:
    keys = {"result": result_json_key(FIRST_BATCH_ID, film_id, "en"), "done": done_key(FIRST_BATCH_ID)}
    return keys.get(failing, MANIFEST)


def _excluded_with_failure(world: RunsWorld, monkeypatch: pytest.MonkeyPatch, failing: str) -> dict[str, str]:
    """Exclude the film while one delete fails; check the request succeeded with the withdrawal pending."""
    ids = seed_leftover(world)
    key = _failing_key(failing, ids[FILM])
    with monkeypatch.context() as patched:
        fail_deleting(patched, key)
        excluded = exclude_film(world, ids)
    flags = (getattr(excluded, "withdrawal_pending", None), pending_of(world) is not None)
    assert (excluded.status, flags, all_present(world, MANIFEST, key)) == ("ok", (True, True), True)
    return ids


def _withdrawn(world: RunsWorld, ids: dict[str, str]) -> bool:
    """Return whether the film's results, `_done.json` and the record are gone and the podcast's results kept."""
    gone = none_present(world, done_key(FIRST_BATCH_ID), *result_keys(FIRST_BATCH_ID, ids[FILM]))
    kept = all_present(world, *result_keys(FIRST_BATCH_ID, ids[PODCAST]))
    return gone and kept and pending_of(world) is None


def _published_items(world: RunsWorld) -> list[str]:
    return [entry.item_id for entry in read_manifest(MANIFEST, read_back(world)).items]


@pytest.mark.parametrize("failing", FAILING)
def test_failed_withdrawal_is_finished_by_the_step(
    runs_world: RunsWorld,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    """Whichever delete fails, the exclusion succeeds; the next manifest step does the remaining deletes first."""
    ids = _excluded_with_failure(runs_world, monkeypatch, failing)
    assert runs_world.step().outcome == "published"
    assert (_published_items(runs_world), _withdrawn(runs_world, ids)) == ([ids[PODCAST]], True)


@pytest.mark.parametrize("failing", FAILING)
def test_failed_withdrawal_is_finished_by_publishing(
    runs_world: RunsWorld,
    monkeypatch: pytest.MonkeyPatch,
    failing: str,
) -> None:
    """`publish_held_manifests` (before a launch) finishes every pending withdrawal, held batch or not."""
    ids = _excluded_with_failure(runs_world, monkeypatch, failing)
    publication = runs_world.publish_held()
    gone = none_present(runs_world, MANIFEST)
    assert (publication.withdrawn, gone, _withdrawn(runs_world, ids)) == ((FIRST_BATCH_ID,), True, True)


def test_two_changes_accumulate_before_the_withdrawal(runs_world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exclusion whose withdrawal failed, then a re-review: both items' results go with the withdrawal."""
    ids = _excluded_with_failure(runs_world, monkeypatch, "manifest")
    still_pending = rereview(runs_world, ids[PODCAST])
    every_result = (*result_keys(FIRST_BATCH_ID, ids[FILM]), *result_keys(FIRST_BATCH_ID, ids[PODCAST]))
    gone = none_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID), *every_result)
    assert (still_pending, gone, pending_of(runs_world)) == (False, True, None)


def test_crash_after_the_manifest_delete(runs_world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A crash after the deletes but before the record is cleared: the next step clears it and publishes."""
    ids = seed_leftover(runs_world)
    with monkeypatch.context() as patched:
        patched.setattr("transcribe_ui.upload.steps.touch_batch", _crash)
        crashed = exclude_film(runs_world, ids)
    assert (crashed.status, none_present(runs_world, MANIFEST), pending_of(runs_world) is not None) == (
        "error",
        True,
        True,
    )
    assert runs_world.step().outcome == "published"
    assert (_published_items(runs_world), _withdrawn(runs_world, ids)) == ([ids[PODCAST]], True)


def test_scope_counts_the_held_version_over_a_leftover(runs_world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """A held batch whose R2 copy is the leftover a pending withdrawal will delete is counted from the database."""
    ids = _excluded_with_failure(runs_world, monkeypatch, "manifest")
    runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    assert (runs_world.step().outcome, batch_state(runs_world)) == ("held", (*HELD[:2], True))
    scope = runs_world.scope()
    counted = [(batch.batch_id, batch.held, [job.item_id for job in batch.pending_jobs]) for batch in scope.batches]
    assert counted == [(FIRST_BATCH_ID, True, [ids[PODCAST]])]
