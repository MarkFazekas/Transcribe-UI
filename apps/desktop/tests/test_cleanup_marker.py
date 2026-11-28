"""The marker of a cleanup of another computer's job (architecture §3.3 "Unknown batch").

A cancel after the marker is written but before the manifest is deleted removes the marker again,
so the job stays as it was; the uploading computer deletes a marker when it reclaims its prefix,
so an interrupted cleanup on another computer cannot purge it later.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from support.cleanup import keys_under
from support.credentials import moto_bucket
from support.launch import FOREIGN_BATCH_ID, foreign_manifest, write_foreign
from support.runs import JSON_TYPE, RunsWorld
from support.upload import upload_world
from transcribe_contracts.layout import audio_key, deleting_key, manifest_key
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.cleanup.stages import Purge, delete_unknown
from transcribe_ui.storage.r2 import R2ObjectStore

# The audio object of the other computer's job.
AUDIO = audio_key(FOREIGN_BATCH_ID, foreign_manifest().items[0].item_id, "flac", None)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunsWorld]:
    """A backend over moto with another computer's published job (manifest and audio)."""
    with moto_bucket(), upload_world(tmp_path / "leg") as created:
        write_foreign(created.store)
        created.store.write(AUDIO, b"{}", JSON_TYPE)
        yield created


class _CancelAfterMarker(R2ObjectStore):
    """The world's store; writing the marker sets the request's cancel flag (a cancel right after the PUT)."""

    def __init__(self, store: R2ObjectStore, flag: CancelFlag) -> None:
        """Delegate to `store`'s client and bucket."""
        super().__init__(store._client, store._bucket)  # noqa: SLF001 -- the test bucket behind the world's store
        self._flag = flag

    def write(self, key: str, body: bytes, content_type: str) -> None:
        """Write, then cancel once the marker is in R2."""
        super().write(key, body, content_type)
        if key == deleting_key(FOREIGN_BATCH_ID):
            self._flag.cancel()


def test_a_cancel_after_the_marker_keeps_the_job(world: RunsWorld) -> None:
    """Cancelled before the manifest delete: the marker is removed again, the manifest and audio stay."""
    flag = CancelFlag()
    purge = Purge(_CancelAfterMarker(world.store, flag), world.context(flag), "cleanup_cloud")
    with pytest.raises(CancelledError):
        delete_unknown(purge, FOREIGN_BATCH_ID)
    left = keys_under(world.store, FOREIGN_BATCH_ID)
    assert (left, purge.deleted) == ([manifest_key(FOREIGN_BATCH_ID), AUDIO], 0)
