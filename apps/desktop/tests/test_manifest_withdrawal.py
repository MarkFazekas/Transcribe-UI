"""Withdrawing a leftover manifest before exclusion or re-review (plan m5 M5.9, architecture §3.3).

A leftover manifest is one the manifest step wrote just before a crash, so the batch is still
`uploading`; a worker may have written `_done.json` and results for it since.
"""

from typing import override

import pytest

from support.batch_backend import error_of, ids_of, run
from support.batches import FILM, FIRST_BATCH_ID, PODCAST, SECOND_BATCH_ID, batch_request
from support.credentials import TEST_BUCKET
from support.runs import JSON_TYPE, MANIFEST, OUTAGE, RUN_ID, RunsWorld, app_pod_name, read_back, seed_uploaded
from support.storage import store_client
from transcribe_contracts.layout import done_key, manifest_key, result_json_key, result_srt_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.api.classes import Response
from transcribe_ui.batches.rereview import return_item_to_review
from transcribe_ui.state.models import BatchRow
from transcribe_ui.storage.constants import ERROR_R2_FAILED
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.manifest import build_manifest, manifest_body

# Language of the seeded jobs.
EN = "en"


def _result_keys(batch_id: str, item_id: str) -> tuple[str, str]:
    return result_srt_key(batch_id, item_id, EN), result_json_key(batch_id, item_id, EN)


def _leftover(world: RunsWorld) -> dict[str, str]:
    """Seed the batch with a leftover manifest of ours, `_done.json`, both items' results and a neighbour batch."""
    ids = seed_uploaded(world)
    with world.ports.state.session() as session:
        body = manifest_body(build_manifest(session, FIRST_BATCH_ID))
    world.store.write(MANIFEST, body, JSON_TYPE)
    worker_keys = [done_key(FIRST_BATCH_ID), done_key(SECOND_BATCH_ID), manifest_key(SECOND_BATCH_ID)]
    for item_id in ids.values():
        worker_keys.extend((*_result_keys(FIRST_BATCH_ID, item_id), *_result_keys(SECOND_BATCH_ID, item_id)))
    for key in worker_keys:
        world.store.write(key, b"{}", JSON_TYPE)
    return ids


def _all_present(world: RunsWorld, *keys: str) -> bool:
    return all(world.store.head(key) is not None for key in keys)


def _none_present(world: RunsWorld, *keys: str) -> bool:
    return all(world.store.head(key) is None for key in keys)


def _pending(world: RunsWorld) -> object:
    with world.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        return row.pending_withdrawal


def _neighbour_untouched(world: RunsWorld, ids: dict[str, str]) -> bool:
    neighbour = [done_key(SECOND_BATCH_ID), manifest_key(SECOND_BATCH_ID)]
    for item_id in ids.values():
        neighbour.extend(_result_keys(SECOND_BATCH_ID, item_id))
    return _all_present(world, *neighbour)


def _exclude_film(world: RunsWorld, ids: dict[str, str]) -> Response:
    return run(world.leg, batch_request("exclude_items", item_ids=[ids[FILM]]))


def test_exclusion_withdraws_only_the_excluded_item(runs_world: RunsWorld) -> None:
    """Our manifest, `_done.json` and the excluded item's results go; the kept item's and the neighbour's stay."""
    ids = _leftover(runs_world)
    excluded = _exclude_film(runs_world, ids)
    gone = _none_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID), *_result_keys(FIRST_BATCH_ID, ids[FILM]))
    kept = _all_present(runs_world, *_result_keys(FIRST_BATCH_ID, ids[PODCAST]))
    assert (excluded.status, gone, kept) == ("ok", True, True)
    assert (_neighbour_untouched(runs_world, ids), _pending(runs_world)) == (True, None)
    assert runs_world.step().outcome == "published"
    assert [entry.item_id for entry in read_manifest(MANIFEST, read_back(runs_world)).items] == [ids[PODCAST]]


def test_rereview_withdraws_only_that_item(runs_world: RunsWorld) -> None:
    """Re-review of the film: the manifest, `_done.json` and the film's results go; the podcast's stay."""
    ids = _leftover(runs_world)
    with runs_world.ports.state.locks.batch(FIRST_BATCH_ID):
        return_item_to_review(runs_world.context(), FIRST_BATCH_ID, ids[FILM])
    gone = _none_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID), *_result_keys(FIRST_BATCH_ID, ids[FILM]))
    kept = _all_present(runs_world, *_result_keys(FIRST_BATCH_ID, ids[PODCAST]))
    assert (gone, kept, _neighbour_untouched(runs_world, ids), _pending(runs_world)) == (True, True, True, None)


def test_a_manifest_that_is_not_ours_is_left_alone(runs_world: RunsWorld) -> None:
    """Another computer's batch with the same id: its manifest and results are not ours to delete."""
    ids = _leftover(runs_world)
    runs_world.store.write(MANIFEST, b'{"another": "computer"}', JSON_TYPE)
    assert _exclude_film(runs_world, ids).status == "ok"
    film_results = _result_keys(FIRST_BATCH_ID, ids[FILM])
    assert _all_present(runs_world, MANIFEST, done_key(FIRST_BATCH_ID), *film_results)


def test_withdrawal_refused_while_a_run_is_active(runs_world: RunsWorld) -> None:
    """A live pod: the exclusion is refused with `run_active` naming the run, and nothing changes."""
    ids = _leftover(runs_world)
    runs_world.cloud.api.pods.add(app_pod_name(RUN_ID, runs_world.now()))
    code, details = error_of(_exclude_film(runs_world, ids))
    assert (code, details["run_ids"]) == ("run_active", [RUN_ID])
    film_results = _result_keys(FIRST_BATCH_ID, ids[FILM])
    assert (sorted(ids_of(runs_world.leg)), _all_present(runs_world, MANIFEST, *film_results)) == (
        sorted((FILM, PODCAST)),
        True,
    )


def test_withdrawal_refused_when_the_cloud_is_unknown(runs_world: RunsWorld) -> None:
    """The activity check failing is a refusal too (fail closed): nothing changes."""
    ids = _leftover(runs_world)
    runs_world.cloud.api.fail("GET", "/v2/pods", *OUTAGE)
    code, _details = error_of(_exclude_film(runs_world, ids))
    assert (code, sorted(ids_of(runs_world.leg)), _all_present(runs_world, MANIFEST)) == (
        "cloud_unreachable",
        sorted((FILM, PODCAST)),
        True,
    )


class ManifestDeleteFails(R2ObjectStore):
    """The test bucket, but deleting a manifest fails."""

    def __init__(self) -> None:
        """Use the test bucket."""
        super().__init__(store_client(), TEST_BUCKET)

    @override
    def delete(self, key: str) -> None:
        if key == MANIFEST:
            raise StorageError(ERROR_R2_FAILED, "delete", "Injected")
        super().delete(key)


def test_failed_withdrawal_is_finished_by_the_step(runs_world: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The withdrawal fails after the exclusion committed: it stays pending, and the manifest step finishes it first."""
    ids = _leftover(runs_world)
    monkeypatch.setattr("transcribe_ui.batches.exclusion.object_store", lambda _ports: ManifestDeleteFails())
    code, _details = error_of(_exclude_film(runs_world, ids))
    assert (code, sorted(ids_of(runs_world.leg)), _pending(runs_world) is not None) == (
        ERROR_R2_FAILED,
        [PODCAST],
        True,
    )
    monkeypatch.undo()
    assert runs_world.step().outcome == "published"
    published = read_manifest(MANIFEST, read_back(runs_world))
    kept = _all_present(runs_world, *_result_keys(FIRST_BATCH_ID, ids[PODCAST]))
    assert ([entry.item_id for entry in published.items], _pending(runs_world), kept) == (
        [ids[PODCAST]],
        None,
        True,
    )
    assert _none_present(runs_world, done_key(FIRST_BATCH_ID), *_result_keys(FIRST_BATCH_ID, ids[FILM]))
