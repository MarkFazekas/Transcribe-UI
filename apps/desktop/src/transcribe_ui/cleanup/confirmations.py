"""The confirmations a cleanup of a known batch needs (architecture §3.3 "Cloud cleanup").

`confirm` is needed, and for `everything` on a batch whose results may not be downloaded yet
also `confirm_not_downloaded` when a transcribe job's subtitle may not be downloaded yet: the
batch is `ready` or `transcribed` and a job that did not fail has no placement outcome
(`placement/recording.py`, the rule that moves a batch to `downloaded`).
"""

from typing import TYPE_CHECKING

from transcribe_ui.api.classes import Confirmation
from transcribe_ui.cleanup.classes import CleanupCloudRequest
from transcribe_ui.cleanup.constants import (
    CONFIRM_FIELD,
    CONFIRM_NOT_DOWNLOADED_FIELD,
    CONFIRMATION_CLEANUP_AUDIO,
    CONFIRMATION_CLEANUP_EVERYTHING,
    CONFIRMATION_NOT_DOWNLOADED,
    NOT_DOWNLOADED_PHASES,
)
from transcribe_ui.cleanup.targets import CleanupTarget

if TYPE_CHECKING:
    from pydantic import JsonValue


def missing_confirmations(request: CleanupCloudRequest, target: CleanupTarget) -> tuple[Confirmation, ...]:
    """Return the confirmations the request still lacks (empty: it may go ahead)."""
    details: dict[str, JsonValue] = {"batch_id": target.batch_id, "scope": request.scope, "phase": target.phase}
    needed: list[Confirmation] = []
    if not request.confirm:
        code = CONFIRMATION_CLEANUP_AUDIO if request.scope == "audio" else CONFIRMATION_CLEANUP_EVERYTHING
        message = _message(request)
        confirm = Confirmation(
            code=code,
            field=CONFIRM_FIELD,
            set_to=True,
            message=message,
            details=details,
        )
        needed.append(confirm)
    if request.scope == "everything" and _not_downloaded(target) and not request.confirm_not_downloaded:
        message = "The batch's subtitles may not be downloaded yet; after this, only the backup on the cloud volume keeps them."
        not_downloaded = Confirmation(
            code=CONFIRMATION_NOT_DOWNLOADED,
            field=CONFIRM_NOT_DOWNLOADED_FIELD,
            set_to=True,
            message=message,
            details=details,
        )
        needed.append(not_downloaded)
    return tuple(needed)


def _not_downloaded(target: CleanupTarget) -> bool:
    return target.phase in NOT_DOWNLOADED_PHASES and target.jobs_not_downloaded > 0


def _message(request: CleanupCloudRequest) -> str:
    if request.scope == "audio":
        return "The batch's audio will be deleted from the cloud; its subtitles stay, but it can no longer be retried."
    return "All of the batch's data will be deleted from the cloud: audio, subtitles and its description."
