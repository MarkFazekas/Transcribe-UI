"""The confirmations of the batch requests that delete local work: `delete_batch`, `exclude_items`.

Without `confirm`, these requests answer `needs_confirmation` after their own checks and change
nothing; every frontend confirms the same way (plan m6 §1 "Confirmations are data, always").
"""

from typing import TYPE_CHECKING

from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.batches.classes import DeleteBatchRequest, ExcludeItemsRequest
from transcribe_ui.batches.constants import CONFIRM_FIELD

if TYPE_CHECKING:
    from pydantic import JsonValue


def delete_confirmation(request: DeleteBatchRequest, phase: str, item_count: int) -> NeedsConfirmation:
    """Return the confirmation of deleting the batch (details: the batch, its phase and item count)."""
    details: dict[str, JsonValue] = {"batch_id": request.batch_id, "phase": phase, "items": item_count}
    message = f"The batch and its {item_count} file(s) will be deleted from this computer's job list."
    confirmation = Confirmation(field=CONFIRM_FIELD, set_to=True, message=message, details=details)
    return NeedsConfirmation(action=request.action, confirmations=(confirmation,))


def exclude_confirmation(request: ExcludeItemsRequest, phase: str, *, uploaded_audio: bool) -> NeedsConfirmation:
    """Return the confirmation of excluding the items.

    Details: whether audio uploaded for them (recorded audio objects) is deleted from the cloud.
    """
    count = len(request.item_ids)
    details: dict[str, JsonValue] = {"batch_id": request.batch_id, "phase": phase, "items": count}
    details["deletes_uploaded_audio"] = uploaded_audio
    message = f"{count} file(s) will be removed from the batch."
    if uploaded_audio:
        message = f"{message} Their audio already uploaded to the cloud is deleted."
    confirmation = Confirmation(field=CONFIRM_FIELD, set_to=True, message=message, details=details)
    return NeedsConfirmation(action=request.action, confirmations=(confirmation,))
