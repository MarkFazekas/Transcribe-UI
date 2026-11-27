"""Reading the manifest of a batch this database does not know, before its cleanup (Q28)."""

from transcribe_contracts.classes import Manifest
from transcribe_contracts.errors import ContractError
from transcribe_contracts.layout import manifest_key
from transcribe_contracts.reading import read_manifest
from transcribe_ui.cleanup.errors import ManifestUnreadableError
from transcribe_ui.state.errors import BatchNotFoundError
from transcribe_ui.storage.r2 import ObjectStore


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
