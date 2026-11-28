"""The deletes of a cloud cleanup, stage by stage (architecture §3.3 "Cloud cleanup").

Every stage is resumable: an object that is gone already is not an error and not counted, and
each stage lists what is left, so repeating an interrupted cleanup finishes it. The cancel token
is checked before every delete.

- `everything` of a batch this database knows: open multipart uploads aborted, then the manifest
  (so no worker can pick the batch up again), `_done.json`, the results, and everything else
  under the batch's prefix (its audio).
- `audio`: the open multipart uploads and the objects of the batch's audio folder.
- `everything` of a batch this database does not know (Q28): the marker
  `<id>/_deleting.json` (`layout.deleting_key`) is written first; then open multipart uploads
  are aborted, the manifest deleted (the batch disappears for listings, adoptions and launches),
  `_done.json` (so no refresh moves a half-deleted batch on), the results and everything else
  under the prefix, and the marker last. A marker without a manifest is what lets a repeated
  request resume an interrupted cleanup (`cleanup/unknown.py`), a transcribed batch or not; an
  uploader never writes it, and deletes it when it reclaims its prefix.
"""

import json
import logging
from collections.abc import Collection
from dataclasses import dataclass

from transcribe_contracts.constants import AUDIO_FOLDER
from transcribe_contracts.layout import batch_prefix, deleting_key, done_key, manifest_key, results_prefix
from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.constants import CLEANUP_STAGE, DELETING_CONTENT_TYPE
from transcribe_ui.storage.errors import StorageError
from transcribe_ui.storage.r2 import ObjectStore

logger = logging.getLogger(__name__)


@dataclass
class Purge:
    """Deletes objects for one request, counting them and reporting progress."""

    store: ObjectStore
    context: RequestContext
    action: str
    deleted: int = 0

    def key(self, key: str) -> None:
        """Delete one object if it exists.

        Raises:
            CancelledError: Cancelled before the delete.
            StorageError: R2 failed.
        """
        if self.store.head(key) is not None:
            self._delete(key)

    def listed(self, prefix: str, keep: Collection[str] = ()) -> None:
        """Delete every object under the prefix except those in `keep`.

        Raises:
            CancelledError: Cancelled between two deletes.
            StorageError: R2 failed.
        """
        for found in self.store.list_objects(prefix):
            if found.key not in keep:
                self._delete(found.key)

    def _delete(self, key: str) -> None:
        self.context.check_cancelled()
        self.store.delete(key)
        self.deleted += 1
        event = ProgressEvent(action=self.action, stage=CLEANUP_STAGE, completed=self.deleted)
        self.context.progress.emit(event)


def delete_everything(purge: Purge, batch_id: str) -> int:
    """Delete every object of a known batch, the manifest first; return the multipart uploads aborted."""
    prefix = batch_prefix(batch_id)
    aborted = purge.store.abort_uploads(prefix)
    purge.key(manifest_key(batch_id))
    purge.key(done_key(batch_id))
    purge.listed(results_prefix(batch_id))
    purge.listed(prefix)
    return aborted


def delete_audio(purge: Purge, batch_id: str) -> int:
    """Delete the batch's audio objects; return the multipart uploads aborted."""
    folder = f"{batch_prefix(batch_id)}{AUDIO_FOLDER}/"
    aborted = purge.store.abort_uploads(folder)
    purge.listed(folder)
    return aborted


def delete_unknown(purge: Purge, batch_id: str) -> int:
    """Delete every object of a batch this database does not know: the marker first, the manifest next, the marker last.

    Return the multipart uploads aborted. The marker is not counted as a deleted object. A
    cancel before the manifest delete removes the marker again (best effort; the refresh's
    `cleanup/pending.py` is the fallback), so the batch stays as it was.

    Raises:
        CancelledError: Cancelled before the marker or between two deletes.
        StorageError: R2 failed (repeating the request, or the refresh, resumes).
    """
    marker = deleting_key(batch_id)
    store = purge.store
    purge.context.check_cancelled()
    body = json.dumps({"batch_id": batch_id}).encode()
    store.write(marker, body, DELETING_CONTENT_TYPE)
    aborted = store.abort_uploads(batch_prefix(batch_id))
    try:
        purge.key(manifest_key(batch_id))
    except CancelledError:
        drop_marker(store, batch_id)
        raise
    finish_unknown(purge, batch_id)
    return aborted


def finish_unknown(purge: Purge, batch_id: str) -> None:
    """Delete what is left of a batch whose manifest is gone: `_done.json` first, then the rest, the marker last.

    `_done.json` goes right after the manifest (as in `delete_everything`), so a refresh never
    moves a half-deleted batch to `transcribed`.

    Raises:
        CancelledError: Cancelled between two deletes.
        StorageError: R2 failed.
    """
    marker = deleting_key(batch_id)
    purge.key(done_key(batch_id))
    purge.listed(results_prefix(batch_id))
    purge.listed(batch_prefix(batch_id), keep=(marker,))
    purge.context.check_cancelled()
    purge.store.delete(marker)


def drop_marker(store: ObjectStore, batch_id: str) -> None:
    """Delete a cleanup's marker, best effort (a failure is logged; the refresh deletes it later)."""
    try:
        store.delete(deleting_key(batch_id))
    except StorageError:
        logger.warning("The cleanup marker of %s could not be deleted; the next refresh deletes it", batch_id)
