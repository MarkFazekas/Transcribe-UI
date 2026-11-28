"""Cleaning up a batch this database does not know: another computer's, or an earlier database's (Q28).

The request's batch id is a `BatchId` (the R2 layout's pattern, checked when the request is read).
Before anything is deleted, `batch_metadata/<id>.json` must read as a manifest through
`reading.read_manifest`: the prefix is then a published batch, not, say, another computer's upload
in progress. Only `everything` is possible (one confirmation), refused while a live run lease
lists the batch. The deletes start with the marker `<id>/_deleting.json`, then the manifest, so
no listing, adoption or launch sees the batch meanwhile, and end with the marker
(`cleanup/stages.py`): a marker without a manifest is the proof that an interrupted cleanup left
the rest, so repeating the request resumes it from the listing, after its own confirmation
(`cleanup_unknown_leftover`); the cleanup is also recorded locally (`cleanup/pending.py`), so the
refresh finishes it without a question. A prefix with neither a manifest nor the marker is never touched
(`batch_not_found`): it may be another computer's upload in progress, and an uploader never
writes the marker.

When the cloud holds another computer's batch under the id of a batch this database knows (the
known rule, architecture §3.9 "Known batches"), the cleanup of the known batch is diverted here
(`diverted`): the confirmation of the known batch does not cover it, so its own confirmation
is asked on the field `confirm_unknown`.
"""

from typing import TYPE_CHECKING

from transcribe_contracts.classes import Manifest
from transcribe_contracts.layout import deleting_key
from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.cleanup.classes import CleanupCloudRequest, CleanupCloudResponse
from transcribe_ui.cleanup.constants import (
    CONFIRM_FIELD,
    CONFIRM_UNKNOWN_FIELD,
    CONFIRMATION_UNKNOWN_BATCH,
    CONFIRMATION_UNKNOWN_ELSEWHERE,
    CONFIRMATION_UNKNOWN_LEFTOVER,
    UPLOADED_ELSEWHERE,
)
from transcribe_ui.cleanup.manifests import find_unknown_manifest
from transcribe_ui.cleanup.pending import drop_pending, record_pending
from transcribe_ui.cleanup.stages import Purge, delete_unknown
from transcribe_ui.runs.cloud import open_store
from transcribe_ui.runs.leases import refuse_if_leased
from transcribe_ui.state.errors import BatchNotFoundError
from transcribe_ui.storage.r2 import ObjectStore

if TYPE_CHECKING:
    from pydantic import JsonValue


def cleanup_unknown(
    request: CleanupCloudRequest,
    context: RequestContext,
    *,
    diverted: bool = False,
) -> CleanupCloudResponse | NeedsConfirmation:
    """Delete every object of a batch this database does not know, once its manifest reads.

    The caller holds the runs lock and the batch id's lock. `diverted`: the request named a batch
    this database knows, and the cloud holds another one under its id.

    Raises:
        BatchNotFoundError: The scope is `audio`, or R2 has neither a manifest nor the marker
            of an interrupted cleanup under the id.
        ManifestUnreadableError: The manifest cannot be read (nothing was deleted).
        RunActiveError: A live run lease lists the batch (nothing was deleted).
        DomainError: R2 failed or the request was cancelled; repeating it resumes.
    """
    batch_id = request.batch_id
    if request.scope != "everything":
        raise BatchNotFoundError(batch_id)
    store = open_store(context)
    asked = _asked(store, batch_id, context.ports.installation.current())
    confirmed = request.confirm_unknown if diverted else request.confirm
    if not confirmed:
        field = CONFIRM_UNKNOWN_FIELD if diverted else CONFIRM_FIELD
        confirmation = asked.model_copy(update={"field": field})
        return NeedsConfirmation(action=request.action, confirmations=(confirmation,))
    refuse_if_leased(store, batch_id, context.ports.clock.now())
    purge = Purge(store, context, request.action)
    # Recorded before the marker and dropped after it: an interruption is finished by the refresh.
    record_pending(context.ports.state, batch_id, context.ports.clock.now())
    aborted = delete_unknown(purge, batch_id)
    drop_pending(context.ports.state, batch_id)
    return CleanupCloudResponse(
        batch_id=batch_id,
        scope=request.scope,
        batch=None,
        deleted_objects=purge.deleted,
        aborted_uploads=aborted,
    )


def _asked(store: ObjectStore, batch_id: str, installation_id: str) -> Confirmation:
    """Return the confirmation the deletion needs (its field is set by the caller).

    Raises:
        BatchNotFoundError: Neither a manifest nor an interrupted cleanup's marker.
        ManifestUnreadableError: The manifest cannot be read.
    """
    manifest = find_unknown_manifest(store, batch_id)
    if manifest is not None:
        return _confirmation(manifest, installation_id)
    if store.head(deleting_key(batch_id)) is None:
        raise BatchNotFoundError(batch_id)
    message = "An interrupted cleanup left data of this batch in the cloud; the rest will be deleted."
    details: dict[str, JsonValue] = {"batch_id": batch_id, "known": False}
    return Confirmation(
        code=CONFIRMATION_UNKNOWN_LEFTOVER,
        field=CONFIRM_FIELD,
        set_to=True,
        message=message,
        details=details,
    )


def _confirmation(manifest: Manifest, installation_id: str) -> Confirmation:
    """Return the question; the stronger form unless the manifest names this installation as its uploader (Q35)."""
    message = "This batch is not known on this computer; all its data will be deleted from the cloud."
    code = CONFIRMATION_UNKNOWN_BATCH
    if manifest.uploaded_by != installation_id:
        code = CONFIRMATION_UNKNOWN_ELSEWHERE
        message = f"{message} {UPLOADED_ELSEWHERE}"
    details: dict[str, JsonValue] = {"batch_id": manifest.batch_id, "files": len(manifest.items), "known": False}
    return Confirmation(
        code=code,
        field=CONFIRM_FIELD,
        set_to=True,
        message=message,
        details=details,
    )
