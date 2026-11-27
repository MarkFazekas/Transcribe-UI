"""Request models of the batches and the review step (architecture §3.3); responses are in `responses.py`."""

from collections.abc import Hashable, Sequence
from typing import Annotated, Literal, Self

from pydantic import Field, NonNegativeInt, model_validator

from transcribe_contracts.classes import BatchId, Ulid
from transcribe_ui.api.base import FrozenModel, LocalPath, WhisperLanguage
from transcribe_ui.discovery.classes import ScanSpec, SelectionOperation


def _unique(entries: Sequence[Hashable], what: str) -> None:
    if len(set(entries)) != len(entries):
        msg = f"every {what} must appear only once"
        raise ValueError(msg)


# Target languages of a file: Whisper languages, at least one (uniqueness is checked by the model).
type TargetLanguages = Annotated[tuple[WhisperLanguage, ...], Field(min_length=1)]


class ItemLanguages(FrozenModel):
    """Target languages of one selected file, instead of the batch's languages."""

    path: LocalPath = Field(description="The file's path exactly as the scan reported it.")
    languages: TargetLanguages

    @model_validator(mode="after")
    def _check_languages(self) -> Self:
        _unique(self.languages, "language")
        return self


class BatchComposition(FrozenModel):
    """The selection of a batch: a scan, its selection operations and the target languages."""

    scan: ScanSpec
    scan_id: Ulid | None = Field(
        default=None,
        description="Reuse this scan of the same options if this process still keeps it; else scan again.",
    )
    operations: tuple[SelectionOperation, ...] = Field(min_length=1)
    languages: TargetLanguages = Field(description="Target languages of every selected file.")
    item_languages: tuple[ItemLanguages, ...] = Field(
        default=(),
        description="Other target languages for single files.",
    )
    confirm_existing_languages: bool = Field(
        default=False,
        description="Create jobs also for languages a file already has a subtitle in.",
    )

    @model_validator(mode="after")
    def _check_unique(self) -> Self:
        _unique(self.languages, "language")
        _unique(tuple(entry.path for entry in self.item_languages), "item_languages path")
        return self


class CreateBatchRequest(BatchComposition):
    """Store a new batch in `composing` from a selection."""

    action: Literal["create_batch"]


class EditBatchRequest(BatchComposition):
    """Replace the items of a batch in `composing` with a new selection."""

    action: Literal["edit_batch"]
    batch_id: BatchId


class DeleteBatchRequest(FrozenModel):
    """Delete a batch in `composing` or `reviewing` (nothing of it is in the cloud yet); needs `confirm`."""

    action: Literal["delete_batch"]
    batch_id: BatchId
    confirm: bool = Field(default=False, description="Delete the batch; without it the answer asks first.")


class StartReviewRequest(FrozenModel):
    """Move a batch from `composing` to `reviewing`."""

    action: Literal["start_review"]
    batch_id: BatchId


class ReviewFilter(FrozenModel):
    """The main view's filters applied to the review views."""

    languages: tuple[WhisperLanguage, ...] = Field(
        default=(),
        description="Only jobs of these languages (all when empty).",
    )
    folder: LocalPath | None = Field(default=None, description="Only files at or below this folder.")


class ReviewBatchRequest(FrozenModel):
    """Probe the files waiting for review, then return the review views."""

    action: Literal["review_batch"]
    batch_id: BatchId
    filters: ReviewFilter = ReviewFilter()


class TranscribeChoice(FrozenModel):
    """Transcribe the file into the job's language from one audio track."""

    kind: Literal["transcribe"]
    item_id: Ulid
    language: WhisperLanguage
    track_index: NonNegativeInt | None = Field(
        default=None,
        description="Audio track of a video file; none for an audio file (uploaded as it is).",
    )


class ExtractChoice(FrozenModel):
    """Extract an embedded text subtitle stream as the job's subtitle."""

    kind: Literal["extract"]
    item_id: Ulid
    language: WhisperLanguage
    stream_index: NonNegativeInt


# One choice of the review step, discriminated on `kind`.
type ReviewChoice = Annotated[TranscribeChoice | ExtractChoice, Field(discriminator="kind")]


class ResolveReviewRequest(FrozenModel):
    """Set the choice of jobs in review; all choices are applied, or none."""

    action: Literal["resolve_review"]
    batch_id: BatchId
    choices: tuple[ReviewChoice, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique(self) -> Self:
        targets = [(choice.item_id, choice.language) for choice in self.choices]
        _unique(targets, "item and language")
        return self


class ExcludeItemsRequest(FrozenModel):
    """Remove items from a batch in `reviewing` or `uploading`, deleting their uploaded audio; needs `confirm`."""

    action: Literal["exclude_items"]
    batch_id: BatchId
    item_ids: tuple[Ulid, ...] = Field(min_length=1)
    confirm: bool = Field(default=False, description="Remove the items; without it the answer asks first.")

    @model_validator(mode="after")
    def _check_unique(self) -> Self:
        _unique(self.item_ids, "item id")
        return self


class ListBatchesRequest(FrozenModel):
    """List the batches, newest first."""

    action: Literal["list_batches"]
    include_hidden: bool = False


class GetBatchRequest(FrozenModel):
    """Return one batch with its items and jobs."""

    action: Literal["get_batch"]
    batch_id: BatchId


class SetBatchHiddenRequest(FrozenModel):
    """Hide a finished batch from the default list, or show it again."""

    action: Literal["set_batch_hidden"]
    batch_id: BatchId
    hidden: bool
