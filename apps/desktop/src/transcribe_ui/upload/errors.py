"""Errors of the manifest step."""

from collections.abc import Collection

from pydantic import JsonValue

from transcribe_ui.api.errors import DomainError
from transcribe_ui.upload.constants import (
    ERROR_AUDIO_UNUSABLE,
    ERROR_BATCH_ID_TAKEN,
    ERROR_COPY_SOURCE_MISSING,
    ERROR_NOTHING_TO_TRANSCRIBE,
    ERROR_REVIEW_INCOMPLETE,
    ERROR_SOURCE_UNREADABLE,
    ERROR_UPLOAD_INCOMPLETE,
    ERROR_UPLOAD_UNVERIFIED,
)


class UploadIncompleteError(DomainError):
    """The batch cannot get its manifest yet: items are in review or audio objects are not verified."""

    def __init__(self, batch_id: str, item_ids: Collection[str]) -> None:
        """Name the items that are not uploaded, or whose audio is missing or not verified (sorted)."""
        super().__init__(
            ERROR_UPLOAD_INCOMPLETE,
            "The batch's upload is not complete yet, so it gets no manifest; finish the upload first.",
            {"batch_id": batch_id, "item_ids": _sorted(item_ids)},
        )


def _sorted(texts: Collection[str]) -> list[JsonValue]:
    found: list[JsonValue] = []
    found.extend(sorted(texts))
    return found


class NothingToTranscribeError(DomainError):
    """The batch has no `transcribe` job: it takes the extract-only path, without a manifest."""

    def __init__(self, batch_id: str) -> None:
        """Name the batch in the details."""
        super().__init__(
            ERROR_NOTHING_TO_TRANSCRIBE,
            "The batch has nothing to transcribe, so it gets no manifest.",
            {"batch_id": batch_id},
        )


class BatchIdTakenError(DomainError):
    """R2 already holds objects under the batch's id, so its upload cannot start; nothing was changed."""

    def __init__(self, batch_id: str) -> None:
        """Name the batch."""
        super().__init__(
            ERROR_BATCH_ID_TAKEN,
            "The cloud already holds data under this batch's id (another computer's batch, or one of an earlier "
            "database); delete this batch and create it again, which gives it a new id.",
            {"batch_id": batch_id},
        )


class ReviewIncompleteError(DomainError):
    """An item of the batch is not resolved, so its upload cannot start; nothing was changed."""

    def __init__(self, batch_id: str, item_ids: Collection[str]) -> None:
        """Name the unresolved items (sorted)."""
        super().__init__(
            ERROR_REVIEW_INCOMPLETE,
            "The review is not complete: resolve or exclude every file first.",
            {"batch_id": batch_id, "item_ids": _sorted(item_ids)},
        )


class UploadUnverifiedError(DomainError):
    """An audio object is missing in R2 after its upload or copy, or has the wrong size; re-sending uploads it again."""

    def __init__(self, object_key: str) -> None:
        """Name the object."""
        super().__init__(
            ERROR_UPLOAD_UNVERIFIED,
            "An uploaded audio object could not be verified in the cloud; send the request again to upload it again.",
            {"object_key": object_key},
        )


class AudioUnusableError(DomainError):
    """A file's audio cannot be uploaded as it is: the file is empty, or its extension is not allowed in R2 keys."""

    def __init__(self, item_id: str) -> None:
        """Name the item."""
        super().__init__(
            ERROR_AUDIO_UNUSABLE,
            "This file's audio cannot be uploaded (it is empty, or its extension is not usable); exclude it.",
            {"item_id": item_id},
        )


class SourceUnreadableError(DomainError):
    """An item's file cannot be read right now, though nothing shows it changed; re-sending resumes."""

    def __init__(self, item_id: str) -> None:
        """Name the item."""
        super().__init__(
            ERROR_SOURCE_UNREADABLE,
            "A file of the batch cannot be read right now (is its drive or network share connected?); "
            "send the request again once it can.",
            {"item_id": item_id},
        )


class CopySourceMissingError(DomainError):
    """A retry item has no copy source for an audio track its jobs use."""

    def __init__(self, item_id: str, track_index: int | None) -> None:
        """Name the item and the track (None: an audio file uploaded as it is)."""
        super().__init__(
            ERROR_COPY_SOURCE_MISSING,
            "A retried file has no audio to copy for one of its jobs.",
            {"item_id": item_id, "track_index": track_index},
        )
