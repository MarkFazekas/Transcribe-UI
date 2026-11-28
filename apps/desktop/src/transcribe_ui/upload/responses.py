"""The response of `run_upload` (architecture §3.3 "Upload")."""

from typing import Literal

from pydantic import NonNegativeInt

from transcribe_contracts.classes import Ulid
from transcribe_ui.batches.responses import BatchSummary, OkResponse

# Where `run_upload` left the batch: manifest published (`ready`), extract-only batch done
# (`transcribed`), or items still in review (no manifest yet).
type UploadOutcome = Literal["ready", "transcribed", "items_in_review"]


class RunUploadResponse(OkResponse):
    """What the upload did, and where the batch stands now."""

    action: Literal["run_upload"] = "run_upload"
    batch: BatchSummary
    outcome: UploadOutcome
    # Items in review, in selection order: sent back now because their file changed, or not resolved yet.
    items_in_review: tuple[Ulid, ...]
    # Audio objects uploaded (or copied, for a retry batch) by this request.
    uploaded_objects: NonNegativeInt
    # Embedded subtitles extracted by this request.
    extracted_subtitles: NonNegativeInt
    # Audio objects under the batch's prefix that no item records, deleted (left by an interruption).
    deleted_objects: NonNegativeInt
    # The sweep of those objects was put off: a leftover manifest's withdrawal is pending and a live
    # run lease lists the batch; the next `run_upload` sweeps.
    sweep_skipped: bool
