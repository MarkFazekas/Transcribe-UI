"""Errors of the manifest step."""

from collections.abc import Collection

from pydantic import JsonValue

from transcribe_ui.api.errors import DomainError
from transcribe_ui.upload.constants import ERROR_NOTHING_TO_TRANSCRIBE, ERROR_UPLOAD_INCOMPLETE


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
