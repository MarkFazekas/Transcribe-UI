"""`adopt_cloud_batch`: record another computer's job in the local history (architecture §3.9 "Download of another installation's job").

The manifest is read through `reading.read_manifest` and treated as untrusted input: each item's
path below the scan root is split with the manifest's own path flavour, and the manifest is
refused (`manifest_path_invalid`) when a part could not be a single folder or file name on every
OS (`placement/naming.py` `unsafe_part`). An id this database already uses is refused
(`batch_exists`): ids are unique only per database. Without `confirm` the request answers
`needs_confirmation` (one confirmation) and changes nothing.

The batch is recorded `ready` (a later `refresh_status` reads its results and `_done.json`), its
creation time the manifest's, its payload marking it adopted with the manifest's `uploaded_by`.
Each item keeps its `original_path` as stored and its relative path; its jobs are pending and its
audio objects recorded as verified (the remaining-time estimate reads their durations). From
then on it is followed and downloaded like any batch; retry, the audio cleanup and extracted
subtitles do not apply to it (`batches/actions.py`).
"""

from typing import TYPE_CHECKING

from transcribe_contracts.classes import Manifest
from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.batches.adopted_rows import record_adopted, relative_paths, require_new
from transcribe_ui.batches.classes import AdoptCloudBatchRequest
from transcribe_ui.batches.cloud_responses import AdoptCloudBatchResponse
from transcribe_ui.batches.constants import CONFIRM_FIELD, CONFIRMATION_ADOPT
from transcribe_ui.batches.phases import load_batch
from transcribe_ui.batches.records import stored_items, summary_of
from transcribe_ui.cleanup.manifests import read_unknown_manifest
from transcribe_ui.runs.cloud import open_store

if TYPE_CHECKING:
    from pydantic import JsonValue


def adopt_cloud_batch(
    request: AdoptCloudBatchRequest, context: RequestContext
) -> AdoptCloudBatchResponse | NeedsConfirmation:
    """Record another computer's batch from its manifest in R2, or ask for the confirmation first.

    Raises:
        BatchBusyError: Another request works on a batch of that id.
        BatchExistsError: This database already has a batch of that id (nothing was changed).
        BatchNotFoundError: R2 holds no manifest under the id.
        ManifestUnreadableError: The manifest does not read (another schema major, invalid).
        ManifestPathInvalidError: A path below the scan root holds an unsafe part.
        StorageError: R2 could not be read.
    """
    state = context.ports.state
    batch_id = request.batch_id
    with state.locks.batch(batch_id):
        with state.session() as session:
            require_new(session, batch_id)
        manifest = read_unknown_manifest(open_store(context), batch_id)
        relative = relative_paths(manifest)
        if not request.confirm:
            return _confirmation(request, manifest)
        with state.session() as session:
            require_new(session, batch_id)
            record_adopted(session, manifest, relative, context.ports.clock.now())
            summary = summary_of(session, load_batch(session, batch_id), stored_items(session, batch_id))
    return AdoptCloudBatchResponse(batch=summary)


def _confirmation(request: AdoptCloudBatchRequest, manifest: Manifest) -> NeedsConfirmation:
    files = len(manifest.items)
    batch_id = manifest.batch_id
    message = (
        f"Add the cloud job {batch_id} ({files} file(s)), uploaded from another computer, to this computer's jobs?"
    )
    details: dict[str, JsonValue] = {"batch_id": batch_id, "files": files}
    found = Confirmation(
        code=CONFIRMATION_ADOPT,
        field=CONFIRM_FIELD,
        set_to=True,
        message=message,
        details=details,
    )
    return NeedsConfirmation(action=request.action, confirmations=(found,))
