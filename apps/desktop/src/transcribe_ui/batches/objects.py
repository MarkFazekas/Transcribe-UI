"""Leaving the upload: forgetting an item's upload rows, then deleting its uploaded audio."""

from collections.abc import Callable, Collection

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from transcribe_contracts.layout import audio_prefix, manifest_key
from transcribe_ui.api.ports import Ports
from transcribe_ui.state.models import AudioObjectRow, ExtractedSubtitleRow, JobRow
from transcribe_ui.storage.client import open_object_store
from transcribe_ui.storage.r2 import ObjectStore


def forget_uploads(session: Session, batch_id: str, item_ids: Collection[str]) -> None:
    """Delete the items' `audio_objects` and `extracted_subtitles` rows (only rows of the batch).

    Done in the transaction that clears `manifest_held`, before any object is deleted: a held
    manifest is then never published with audio that is gone; the objects an interruption
    leaves behind stay under the item's audio prefix until something deletes that prefix.
    """
    wanted = list(item_ids)
    jobs = (JobRow.batch_id == batch_id, JobRow.item_id.in_(wanted))
    job_ids = select(JobRow.job_id).where(*jobs)
    extracted = ExtractedSubtitleRow.job_id.in_(job_ids)
    session.execute(delete(ExtractedSubtitleRow).where(extracted))
    audio = (AudioObjectRow.batch_id == batch_id, AudioObjectRow.item_id.in_(wanted))
    session.execute(delete(AudioObjectRow).where(*audio))


def object_store(ports: Ports) -> ObjectStore:
    """Return the R2 object store of the configured credentials.

    Raises:
        DomainError: `credentials_missing`, `credentials_invalid` or a `StorageError`.
    """
    return open_object_store(ports.credentials, ports.r2_clients)


def delete_item_objects(store: ObjectStore, batch_id: str, item_id: str, check_cancelled: Callable[[], None]) -> int:
    """Abort the item's open multipart uploads, then delete every audio object under its prefix; return how many existed.

    The listing finds the recorded objects and any whose upload finished but was not recorded
    (a crash between the upload and its database row), so the rows are not needed. Missing
    objects are not errors, so a repeated call is harmless. The cancel check runs before each
    delete.

    Raises:
        CancelledError: If cancelled between two deletes (the objects deleted so far stay gone).
    """
    prefix = audio_prefix(batch_id, item_id)
    store.abort_uploads(prefix)
    listed = [found.key for found in store.list_objects(prefix)]
    for key in listed:
        check_cancelled()
        store.delete(key)
    return len(listed)


def withdraw_manifest(store: ObjectStore, batch_id: str) -> None:
    """Delete the manifest of an `uploading` batch before any of its audio is deleted.

    An `uploading` batch has a manifest in R2 only when the manifest step was interrupted
    between writing it and moving the batch to `ready`; its audio list is about to change, so it
    must not stay visible to the worker. Missing is success (the usual case).
    """
    store.delete(manifest_key(batch_id))
