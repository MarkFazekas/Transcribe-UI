"""Domain errors of retry batches and cloud cleanup; the backend turns them into error responses (§6)."""

from transcribe_ui.api.errors import DomainError
from transcribe_ui.cleanup.constants import (
    ERROR_AUDIO_DELETED,
    ERROR_MANIFEST_UNREADABLE,
    ERROR_RESULTS_PENDING,
    ERROR_RETRY_COPYING,
    ERROR_RETRY_JOB_INVALID,
)


class AudioDeletedError(DomainError):
    """The batch's audio was cleaned up, so its jobs cannot be retried; nothing was changed."""

    def __init__(self, batch_id: str) -> None:
        """Name the batch."""
        super().__init__(
            ERROR_AUDIO_DELETED,
            "The batch's audio was deleted from the cloud, so it cannot be retried; create a new batch instead.",
            {"batch_id": batch_id},
        )


class RetryJobInvalidError(DomainError):
    """A job of the retry is not a failed or no-speech transcribe job of the source; nothing was changed."""

    def __init__(self, index: int, reason: str) -> None:
        """Name the job by its index in the request, and why it cannot be retried."""
        super().__init__(
            ERROR_RETRY_JOB_INVALID,
            "Only transcriptions that failed or found no speech can be retried.",
            {"job": index, "reason": reason},
        )


class RetryCopyingError(DomainError):
    """A retry batch of this batch is still copying its audio; nothing was changed."""

    def __init__(self, batch_id: str, retry_batch_id: str) -> None:
        """Name the batch and its retry batch."""
        super().__init__(
            ERROR_RETRY_COPYING,
            "A retry of this batch still copies its audio; finish or clean up the retry batch first.",
            {"batch_id": batch_id, "retry_batch_id": retry_batch_id},
        )


class ResultsPendingError(DomainError):
    """The batch has jobs without a result; deleting its audio would fail them. Nothing was changed."""

    def __init__(self, batch_id: str, pending: int) -> None:
        """Name the batch and how many jobs have no result."""
        super().__init__(
            ERROR_RESULTS_PENDING,
            "Some files of the batch have no result yet, so their audio is still needed; "
            "wait for the transcription, or delete everything instead.",
            {"batch_id": batch_id, "pending_jobs": pending},
        )


class ManifestUnreadableError(DomainError):
    """The manifest of a batch this database does not know cannot be read, so nothing is deleted."""

    def __init__(self, batch_id: str) -> None:
        """Name the batch."""
        super().__init__(
            ERROR_MANIFEST_UNREADABLE,
            "This batch's description in the cloud cannot be read (another app version, or damaged), "
            "so nothing was deleted.",
            {"batch_id": batch_id},
        )
