"""Cleaning up a batch this database does not know: another computer's, or an earlier database's (Q28).

The request's batch id is a `BatchId` (the R2 layout's pattern, checked when the request is read).
Before anything is deleted, `batch_metadata/<id>.json` must read as a manifest through
`reading.read_manifest`: the prefix is then a published batch, not, say, another computer's upload
in progress. Only `everything` is possible (one confirmation), refused while a run is active;
the deletes run with the manifest last (`cleanup/stages.py`), so an interrupted cleanup is
resumed by repeating it.
"""

from typing import TYPE_CHECKING

from transcribe_contracts.classes import Manifest
from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.classes import CleanupCloudRequest, CleanupCloudResponse
from transcribe_ui.cleanup.constants import CONFIRM_FIELD
from transcribe_ui.cleanup.manifests import read_unknown_manifest
from transcribe_ui.cleanup.stages import Purge, delete_unknown
from transcribe_ui.runs.cloud import open_cloud
from transcribe_ui.runs.guards import require_no_run
from transcribe_ui.state.errors import BatchNotFoundError

if TYPE_CHECKING:
    from pydantic import JsonValue


def cleanup_unknown(request: CleanupCloudRequest, context: RequestContext) -> CleanupCloudResponse | NeedsConfirmation:
    """Delete every object of a batch this database does not know, once its manifest reads.

    The caller holds the runs lock and the batch id's lock.

    Raises:
        BatchNotFoundError: The scope is `audio`, or R2 has no manifest under the id either.
        ManifestUnreadableError: The manifest cannot be read (nothing was deleted).
        RunActiveError: A run is active (nothing was deleted).
        ActivityUnknownError: The run activity cannot be checked (nothing was deleted).
        DomainError: R2 failed or the request was cancelled; repeating it resumes.
    """
    batch_id = request.batch_id
    if request.scope != "everything":
        raise BatchNotFoundError(batch_id)
    with open_cloud(context) as cloud:
        manifest = read_unknown_manifest(cloud.store, batch_id)
        if not request.confirm:
            return _confirmation(request, manifest)
        require_no_run(context, cloud)
        purge = Purge(cloud.store, context, request.action)
        aborted = delete_unknown(purge, batch_id)
    return CleanupCloudResponse(
        batch_id=batch_id,
        scope=request.scope,
        batch=None,
        deleted_objects=purge.deleted,
        aborted_uploads=aborted,
    )


def _confirmation(request: CleanupCloudRequest, manifest: Manifest) -> NeedsConfirmation:
    message = "This batch is not known on this computer; all its data will be deleted from the cloud."
    details: dict[str, JsonValue] = {"batch_id": manifest.batch_id, "files": len(manifest.items), "known": False}
    confirmation = Confirmation(field=CONFIRM_FIELD, set_to=True, message=message, details=details)
    return NeedsConfirmation(action=request.action, confirmations=(confirmation,))
