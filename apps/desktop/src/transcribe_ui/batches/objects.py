"""Deleting the uploaded audio of items that leave the upload (excluded, or back in review)."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from transcribe_contracts.layout import audio_prefix
from transcribe_ui.api.ports import Ports
from transcribe_ui.state.models import AudioObjectRow
from transcribe_ui.storage.client import open_object_store
from transcribe_ui.storage.r2 import ObjectStore

# Object keys per item id.
type ObjectKeys = dict[str, tuple[str, ...]]


def known_object_keys(session: Session, item_ids: Iterable[str]) -> ObjectKeys:
    """Return the audio object keys the database records per item."""
    wanted = list(item_ids)
    found: dict[str, list[str]] = {item_id: [] for item_id in wanted}
    owned = AudioObjectRow.item_id.in_(wanted)
    columns = select(AudioObjectRow.item_id, AudioObjectRow.object_key).where(owned)
    for owner, key in session.execute(columns):
        found[owner].append(key)
    return {item_id: tuple(sorted(found[item_id])) for item_id in wanted}


def object_store(ports: Ports) -> ObjectStore:
    """Return the R2 object store of the configured credentials.

    Raises:
        DomainError: `credentials_missing`, `credentials_invalid` or a `StorageError`.
    """
    return open_object_store(ports.credentials, ports.r2_clients)


def delete_item_objects(store: ObjectStore, batch_id: str, item_id: str, known: Iterable[str]) -> int:
    """Delete every audio object of the item: the recorded ones and any other under its prefix.

    Listing the prefix also finds an object whose upload finished but was not recorded (a crash
    between the upload and its database row). Missing objects are not errors, so a repeated
    call is harmless. Return how many keys were deleted.
    """
    listed = {found.key for found in store.list_objects(audio_prefix(batch_id, item_id))}
    keys = sorted(listed | set(known))
    for key in keys:
        store.delete(key)
    return len(keys)
