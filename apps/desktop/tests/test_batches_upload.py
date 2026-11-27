"""Batches past the review on one backend: re-review, exclusion, delete and hide (plan m5 M5.8).

An `uploading` batch is seeded through the state store (the upload is M5.10), with its audio
objects in a moto bucket.
"""

from sqlalchemy import select

from support.batch_backend import (
    REVIEW,
    batch_row,
    context_of,
    error_code,
    error_of,
    ids_of,
    item_phase,
    run,
    run_ok,
    start,
)
from support.batches import (
    FILM,
    FIRST_BATCH_ID,
    MEDIA_ROOT,
    PODCAST,
    SEEDED_OBJECT_BYTES,
    SILENT,
    batch_request,
    list_request,
    seed_uploading_batch,
    seeded_keys,
    transcribe,
)
from support.parity import Leg
from transcribe_contracts.layout import audio_prefix
from transcribe_ui.batches.rereview import return_item_to_review
from transcribe_ui.state.models import BatchRow, ItemRow
from transcribe_ui.storage.r2 import R2ObjectStore

# Target languages of these tests.
EN = "en"
HU = "hu"
# A key under the film's audio prefix that the database does not record (a crash after the upload).
UNRECORDED_SUFFIX = "a0.mka"
# Prefix of every object of the seeded batch.
BATCH_PREFIX = f"{FIRST_BATCH_ID}/"


def _upload(store: R2ObjectStore, keys: list[str]) -> None:
    for key in keys:
        store.write(key, b"a" * SEEDED_OBJECT_BYTES, "audio/x-matroska")


def _listed(store: R2ObjectStore) -> list[str]:
    return [found.key for found in store.list_objects(BATCH_PREFIX)]


def _seeded(leg: Leg, store: R2ObjectStore) -> tuple[dict[str, str], list[str]]:
    ids = seed_uploading_batch(leg.ports.state, leg.root)
    keys = seeded_keys(leg.ports.state)
    _upload(store, keys)
    return ids, keys


def test_rereview_sends_one_item_back(batch_leg: Leg, batch_store: R2ObjectStore) -> None:
    """An item sent back to review loses its audio and the held manifest; the batch stays `uploading`."""
    ids, keys = _seeded(batch_leg, batch_store)
    with batch_leg.ports.state.locks.batch(FIRST_BATCH_ID):
        return_item_to_review(context_of(batch_leg), FIRST_BATCH_ID, ids[FILM])
    row = batch_row(batch_leg)
    assert _listed(batch_store) == [key for key in keys if ids[PODCAST] in key]
    assert (row.phase, row.manifest_held, item_phase(batch_leg, ids[FILM])) == ("uploading", False, "reviewing")


def test_review_and_resolve_serve_the_returned_item(batch_leg: Leg, batch_store: R2ObjectStore) -> None:
    """review_batch probes only the item back in review; resolve_review refuses the uploaded one."""
    ids, _keys = _seeded(batch_leg, batch_store)
    film = ids[FILM]
    with batch_leg.ports.state.locks.batch(FIRST_BATCH_ID):
        return_item_to_review(context_of(batch_leg), FIRST_BATCH_ID, film)
    review = run_ok(batch_leg, REVIEW)["review"]
    run_ok(batch_leg, batch_request("resolve_review", choices=[transcribe(film, EN, 0)]))
    refused = run(batch_leg, batch_request("resolve_review", choices=[transcribe(ids[PODCAST], EN)]))
    assert (review["items_in_review"], review["complete"]) == (1, True)
    assert item_phase(batch_leg, film) == "reviewed"
    assert error_of(refused) == ("choice_invalid", {"choice": 0, "reason": "item_not_in_review"})


def test_exclusion_deletes_the_audio_of_the_item(batch_leg: Leg, batch_store: R2ObjectStore) -> None:
    """Excluding an item deletes its recorded and unrecorded audio objects and clears `manifest_held`."""
    ids, keys = _seeded(batch_leg, batch_store)
    unrecorded = f"{audio_prefix(FIRST_BATCH_ID, ids[FILM])}{UNRECORDED_SUFFIX}"
    _upload(batch_store, [unrecorded])
    deleted = run_ok(batch_leg, batch_request("exclude_items", item_ids=[ids[FILM]]))["deleted_objects"]
    row = batch_row(batch_leg)
    film_keys = [unrecorded, *(key for key in keys if ids[FILM] in key)]
    assert (deleted, _listed(batch_store)) == (len(film_keys), [key for key in keys if ids[PODCAST] in key])
    assert (row.phase, row.manifest_held, list(ids_of(batch_leg))) == ("uploading", False, [PODCAST])


def test_excluding_the_last_item_is_refused(batch_leg: Leg, batch_store: R2ObjectStore) -> None:
    """A batch cannot lose its last item, and unknown items are refused; nothing is deleted."""
    ids, keys = _seeded(batch_leg, batch_store)
    every = run(batch_leg, batch_request("exclude_items", item_ids=list(ids.values())))
    unknown = run(batch_leg, batch_request("exclude_items", item_ids=["01JDZZZZZZZZZZZZZZZZZZZZ99"]))
    assert (error_code(every), error_code(unknown)) == ("last_item", "item_not_found")
    assert (_listed(batch_store), batch_row(batch_leg).manifest_held) == (keys, True)


def test_last_transcribe_item_excluded_leaves_extracts(batch_leg: Leg) -> None:
    """With only `extract` jobs left, the batch is extract-only (no upload, no manifest)."""
    start(batch_leg, HU, paths=(f"{MEDIA_ROOT}/Audio/{PODCAST}", f"{MEDIA_ROOT}/Movies/{SILENT}"))
    run_ok(batch_leg, REVIEW)
    ids = ids_of(batch_leg)
    detail = run_ok(batch_leg, batch_request("exclude_items", item_ids=[ids[PODCAST]]))["batch"]
    review = run_ok(batch_leg, REVIEW)["review"]
    assert (detail["summary"]["extract_only"], review["complete"]) == (True, True)


def test_delete_only_before_the_upload(batch_leg: Leg) -> None:
    """A batch in review can be deleted with its items; one in `uploading` cannot."""
    start(batch_leg, HU)
    deleted = run_ok(batch_leg, batch_request("delete_batch"))["batch_id"]
    with batch_leg.ports.state.session() as session:
        leftover = session.scalars(select(ItemRow)).first()
    seed_uploading_batch(batch_leg.ports.state, batch_leg.root)
    refused = run(batch_leg, batch_request("delete_batch"))
    assert (deleted, leftover) == (FIRST_BATCH_ID, None)
    assert (error_code(refused), batch_row(batch_leg).phase) == ("wrong_phase", "uploading")


def test_hide_only_finished_batches(batch_leg: Leg) -> None:
    """A finished batch can be hidden from the default list and shown again."""
    seed_uploading_batch(batch_leg.ports.state, batch_leg.root, manifest_held=False)
    refused = run(batch_leg, batch_request("set_batch_hidden", hidden=True))
    with batch_leg.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        row.phase = "downloaded"
    run_ok(batch_leg, batch_request("set_batch_hidden", hidden=True))
    default = run_ok(batch_leg, list_request())["batches"]
    hidden = run_ok(batch_leg, list_request(include_hidden=True))["batches"]
    run_ok(batch_leg, batch_request("set_batch_hidden", hidden=False))
    shown = run_ok(batch_leg, list_request())["batches"]
    assert (error_code(refused), default) == ("wrong_phase", [])
    assert [(entry["hidden"], entry["items"]) for entry in hidden] == [(True, 2)]
    assert [entry["hidden"] for entry in shown] == [False]
