"""Requests and responses of retry batches and cloud cleanup (architecture §3.3)."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, NonNegativeInt

from transcribe_contracts.classes import BatchId
from transcribe_ui.api.base import FrozenModel, check_distinct
from transcribe_ui.batches.payloads import CleanupScope, RetryJob
from transcribe_ui.batches.responses import BatchSummary, OkResponse
from transcribe_ui.runs.classes import ActivitySource
from transcribe_ui.upload.responses import UploadOutcome

# The jobs of a retry: at least one, each once.
type RetryJobs = Annotated[tuple[RetryJob, ...], Field(min_length=1), AfterValidator(check_distinct)]
# Where `create_retry_batch` left the retry batch: as `run_upload` (`ready`, or `held` during a run),
# or `past_upload` when the existing retry batch had already left `uploading` (nothing was done).
type RetryOutcome = UploadOutcome | Literal["past_upload"]


# Requests


class CreateRetryBatchRequest(FrozenModel):
    """Retry failed or no-speech jobs of a batch in a new batch (mode `careful`, the audio copied in R2).

    Repeating the request with the same batch and jobs returns that retry batch and resumes it.
    """

    action: Literal["create_retry_batch"]
    # The source batch.
    batch_id: BatchId
    jobs: RetryJobs


class CleanupCloudRequest(FrozenModel):
    """Delete a batch's audio, or all its data, from the cloud; needs `confirm`.

    `everything` on a batch whose results may not be downloaded yet also needs
    `confirm_not_downloaded`. A batch this database does not know (another computer's, Q28)
    can be cleaned up with `everything` once its manifest reads.
    """

    action: Literal["cleanup_cloud"]
    batch_id: BatchId
    scope: CleanupScope
    confirm: bool = False
    confirm_not_downloaded: bool = False


# Responses


class CreateRetryBatchResponse(OkResponse):
    """The retry batch of the source and job set, and how far its copy and manifest went."""

    action: Literal["create_retry_batch"] = "create_retry_batch"
    source_batch_id: BatchId
    # False: the retry batch of this source and job set existed already and was resumed.
    created: bool
    batch: BatchSummary
    outcome: RetryOutcome
    # Audio objects copied by this request.
    copied_objects: NonNegativeInt
    # Objects under the retry batch's prefix that no item records, deleted (left by an interruption).
    deleted_objects: NonNegativeInt
    # The sweep of those objects was put off (see `run_upload`).
    sweep_skipped: bool
    # The signs of the run that holds the manifest back (`held`; empty otherwise).
    activity: tuple[ActivitySource, ...] = ()


class CleanupCloudResponse(OkResponse):
    """What the cleanup deleted, and the batch as stored now."""

    action: Literal["cleanup_cloud"] = "cleanup_cloud"
    batch_id: BatchId
    scope: CleanupScope
    # None for a batch this database does not know (another computer's).
    batch: BatchSummary | None
    # Objects deleted by this request (what was gone already is not counted).
    deleted_objects: NonNegativeInt
    # Open multipart uploads aborted by this request.
    aborted_uploads: NonNegativeInt
