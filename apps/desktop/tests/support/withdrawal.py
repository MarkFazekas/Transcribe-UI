"""A batch with a leftover manifest, and helpers of the withdrawal tests (plan m5 M5.9).

A leftover manifest is one the manifest step wrote just before a crash, so the batch is still
`uploading`; a worker may have written `_done.json` and results for it since.
"""

from typing import override

import pytest

from support.batch_backend import run
from support.batches import FILM, FIRST_BATCH_ID, SECOND_BATCH_ID, batch_request
from support.credentials import TEST_BUCKET
from support.runs import JSON_TYPE, MANIFEST, RunsWorld, seed_uploaded
from support.storage import store_client
from transcribe_contracts.layout import done_key, manifest_key, result_json_key, result_srt_key
from transcribe_ui.api.classes import Response
from transcribe_ui.batches.rereview import return_item_to_review
from transcribe_ui.state.models import BatchRow
from transcribe_ui.storage.constants import ERROR_R2_FAILED
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import R2ObjectStore
from transcribe_ui.upload.manifest import build_manifest, manifest_body

# Language of the seeded jobs.
EN = "en"


def result_keys(batch_id: str, item_id: str) -> tuple[str, str]:
    """Return the subtitle and result JSON keys of an item's job."""
    return result_srt_key(batch_id, item_id, EN), result_json_key(batch_id, item_id, EN)


def seed_leftover(world: RunsWorld) -> dict[str, str]:
    """Seed the batch with a leftover manifest of ours, `_done.json`, both items' results and a neighbour batch."""
    ids = seed_uploaded(world)
    with world.ports.state.session() as session:
        body = manifest_body(build_manifest(session, FIRST_BATCH_ID))
    world.store.write(MANIFEST, body, JSON_TYPE)
    worker_keys = [done_key(FIRST_BATCH_ID), done_key(SECOND_BATCH_ID), manifest_key(SECOND_BATCH_ID)]
    for item_id in ids.values():
        worker_keys.extend((*result_keys(FIRST_BATCH_ID, item_id), *result_keys(SECOND_BATCH_ID, item_id)))
    for key in worker_keys:
        world.store.write(key, b"{}", JSON_TYPE)
    return ids


def all_present(world: RunsWorld, *keys: str) -> bool:
    """Return whether every object exists."""
    return all(world.store.head(key) is not None for key in keys)


def none_present(world: RunsWorld, *keys: str) -> bool:
    """Return whether no object exists."""
    return all(world.store.head(key) is None for key in keys)


def pending_of(world: RunsWorld) -> object:
    """Return the batch's stored `pending_withdrawal`."""
    with world.ports.state.session() as session:
        row = session.get(BatchRow, FIRST_BATCH_ID)
        assert row is not None
        return row.pending_withdrawal


def neighbour_untouched(world: RunsWorld, ids: dict[str, str]) -> bool:
    """Return whether every object of the neighbour batch (`_2`) is still there."""
    neighbour = [done_key(SECOND_BATCH_ID), manifest_key(SECOND_BATCH_ID)]
    for item_id in ids.values():
        neighbour.extend(result_keys(SECOND_BATCH_ID, item_id))
    return all_present(world, *neighbour)


def exclude_film(world: RunsWorld, ids: dict[str, str]) -> Response:
    """Send `exclude_items` for the film."""
    return run(world.leg, batch_request("exclude_items", confirm=True, item_ids=[ids[FILM]]))


def rereview(world: RunsWorld, item_id: str) -> bool:
    """Send the item back to review as the upload does; return whether a withdrawal stays pending."""
    with world.ports.state.locks.batch(FIRST_BATCH_ID):
        return return_item_to_review(world.context(), FIRST_BATCH_ID, item_id)


class DeleteFails(R2ObjectStore):
    """The test bucket, but deleting one key fails."""

    def __init__(self, failing: str) -> None:
        """Fail every delete of this key."""
        super().__init__(store_client(), TEST_BUCKET)
        self.failing = failing

    @override
    def delete(self, key: str) -> None:
        if key == self.failing:
            raise StorageError(ERROR_R2_FAILED, "delete", "Injected")
        super().delete(key)


def fail_deleting(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """Make the exclusion's object store fail to delete this key."""
    monkeypatch.setattr("transcribe_ui.batches.exclusion.object_store", lambda _ports: DeleteFails(key))
