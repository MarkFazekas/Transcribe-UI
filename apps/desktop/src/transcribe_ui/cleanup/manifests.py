"""Reading the manifest under a batch id before a cleanup: is it this database's batch, and the unknown one's (Q28)."""

from transcribe_contracts.classes import Manifest
from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.cleanup.errors import ManifestUnreadableError
from transcribe_ui.state.errors import BatchNotFoundError
from transcribe_ui.state.store import LocalState
from transcribe_ui.storage.r2 import ObjectStore
from transcribe_ui.upload.leftover import is_known


def read_unknown_manifest(store: ObjectStore, batch_id: str) -> Manifest:
    """Return the batch's manifest, read through `reading.read_manifest`.

    Raises:
        BatchNotFoundError: R2 holds no manifest under the id.
        ManifestUnreadableError: It does not read (another schema major, invalid, another batch id).
        StorageError: R2 could not be read.
    """
    key = manifest_key(batch_id)
    body = store.read(key)
    if body is None:
        raise BatchNotFoundError(batch_id)
    try:
        return read_manifest(key, body)
    except ContractError:
        failure = ManifestUnreadableError(batch_id)
    # Raised outside the `except` block, so the manifest's validation error is not chained.
    raise failure


def known_in_cloud(state: LocalState, store: ObjectStore, batch_id: str) -> bool:
    """Return whether the manifest in R2 under the id, if any, is this database's batch (the known rule).

    No manifest, or one that does not read, leaves the batch this database's (its own cleanup
    then runs); a readable manifest of another batch (another creation) makes it unknown.

    Raises:
        StorageError: R2 could not be read.
    """
    key = manifest_key(batch_id)
    body = store.read(key)
    if body is None:
        return True
    try:
        read_manifest(key, body)
    except ContractError:
        return True
    with state.session() as session:
        return is_known(session, batch_id, body)
