"""Response models of the batches and the review step (architecture §3.3)."""

from typing import Literal

from pydantic import AwareDatetime, Field, NonNegativeInt

from transcribe_contracts.classes import BatchId, MediaKind, TranscriptionMode, Ulid
from transcribe_ui.api.base import FrozenModel, LocalPath, PathFlavor
from transcribe_ui.batches.classes import ItemLanguages, TargetLanguages
from transcribe_ui.batches.payloads import CleanupScope, ItemPhase, ItemProblem, JobKind, ResultErrorCode
from transcribe_ui.discovery.classes import ScanSpec, ScanWarning, SelectionOperation
from transcribe_ui.media.classes import AudioTrack, SubtitleStream
from transcribe_ui.state.models import BatchPhase

# A batch request a batch allows now (`BatchSummary.actions`): the request's action, with
# `cleanup_cloud` split by scope (`cleanup_audio`, `cleanup_everything`) and `set_batch_hidden` by
# direction (`hide_batch`, `show_batch`).
type BatchAction = Literal[
    "edit_batch",
    "start_review",
    "delete_batch",
    "review_batch",
    "resolve_review",
    "exclude_items",
    "run_upload",
    "plan_download",
    "run_download",
    "export_custom_plan",
    "import_custom_plan",
    "create_retry_batch",
    "cleanup_audio",
    "cleanup_everything",
    "hide_batch",
    "show_batch",
]


class OkResponse(FrozenModel):
    """Base of the success responses of this package (`status` "ok", written first)."""

    status: Literal["ok"] = "ok"


class JobView(FrozenModel):
    """One target language of an item."""

    language: str
    kind: JobKind
    mode: TranscriptionMode
    track_index: NonNegativeInt | None
    stream_index: NonNegativeInt | None
    status: str
    # Whether the choice is complete and fits the probed file.
    resolved: bool
    # Why a transcribe job failed (`status` `failed`): its result's error, `unreadable result` or
    # `missing result`; None otherwise.
    result_error: str | None
    # The code of `unreadable result` and `missing result` (the frontends show its catalog text);
    # None for the worker's own errors (shown as they are) and for jobs that did not fail.
    result_error_code: ResultErrorCode | None


class BatchItem(FrozenModel):
    """A source file of a batch."""

    item_id: Ulid
    path: LocalPath
    name: str
    kind: MediaKind
    size_bytes: NonNegativeInt
    phase: ItemPhase
    subtitle_languages: tuple[str, ...]
    problem: ItemProblem | None
    jobs: tuple[JobView, ...]


class BatchSummary(FrozenModel):
    """A batch as the batch list shows it."""

    batch_id: BatchId
    phase: BatchPhase
    created_at: AwareDatetime
    updated_at: AwareDatetime
    items: NonNegativeInt  # noqa: WPS110 -- the count of items, named like the list of `BatchDetail`
    jobs: NonNegativeInt
    # Every target language of the batch, sorted.
    languages: tuple[str, ...]
    hidden: bool
    # Upload complete, manifest kept back while a run is active.
    manifest_held: bool
    audio_deleted: bool
    # A cloud cleanup of this scope was interrupted: repeat it (no worker starts meanwhile).
    cleanup_pending: CleanupScope | None
    # Only `extract` jobs: the batch needs no upload and no cloud run.
    extract_only: bool
    # The batch requests whose local conditions (phase, flags, item and job states) hold now, in
    # the order to offer them. A listed request can still be refused for a reason outside the
    # batch (a run is active, the batch is busy, the cloud cannot be reached, the request's own
    # fields).
    actions: tuple[BatchAction, ...]


class BatchSelection(FrozenModel):
    """The selection a batch was composed from: send it back as an `edit_batch` to reopen it."""

    scan: ScanSpec
    operations: tuple[SelectionOperation, ...] = Field(min_length=1)
    languages: TargetLanguages
    item_languages: tuple[ItemLanguages, ...]


class BatchDetail(FrozenModel):
    """A batch with its items and jobs."""

    summary: BatchSummary
    scan_root: LocalPath
    path_flavor: PathFlavor
    # The selection the batch was composed from, to reopen it ("Edit selection"); None for a retry
    # batch and for a batch created before the app kept it.
    composition: BatchSelection | None
    items: tuple[BatchItem, ...]  # noqa: WPS110 -- the batch's items


class ReviewFile(FrozenModel):
    """A file in review: what was probed and the choice of each of its jobs."""

    item_id: Ulid
    path: LocalPath
    name: str
    kind: MediaKind
    phase: ItemPhase
    problem: ItemProblem | None
    audio_tracks: tuple[AudioTrack, ...]
    subtitle_streams: tuple[SubtitleStream, ...]
    jobs: tuple[JobView, ...]


class BatchReview(FrozenModel):
    """The review views of a batch, narrowed by the request's filters."""

    batch_id: BatchId
    phase: BatchPhase
    # Items whose choices can still change (all of them before the upload).
    items_in_review: NonNegativeInt
    # Those not resolved yet: not probed, with a problem or with an unresolved job.
    items_unresolved: NonNegativeInt
    # Every item in review is resolved (the upload can start).
    complete: bool
    # Files with embedded subtitles: transcribe or extract, per language.
    subtitle_view: tuple[ReviewFile, ...]
    # Video files with more than one audio track: which track, per language.
    track_view: tuple[ReviewFile, ...]
    # Files that still need something: a probe, a choice, or to be excluded.
    unresolved: tuple[ReviewFile, ...]


class CreateBatchResponse(OkResponse):
    """The new batch, in `composing`."""

    action: Literal["create_batch"] = "create_batch"
    batch: BatchDetail
    warnings: tuple[ScanWarning, ...]


class EditBatchResponse(OkResponse):
    """The batch with its new items."""

    action: Literal["edit_batch"] = "edit_batch"
    batch: BatchDetail
    warnings: tuple[ScanWarning, ...]


class DeleteBatchResponse(OkResponse):
    """The batch is gone."""

    action: Literal["delete_batch"] = "delete_batch"
    batch_id: BatchId


class StartReviewResponse(OkResponse):
    """The batch, now in `reviewing`."""

    action: Literal["start_review"] = "start_review"
    batch: BatchSummary


class ReviewBatchResponse(OkResponse):
    """The review views after probing."""

    action: Literal["review_batch"] = "review_batch"
    review: BatchReview


class ResolveReviewResponse(OkResponse):
    """The review views (unfiltered) after the choices."""

    action: Literal["resolve_review"] = "resolve_review"
    review: BatchReview


class ExcludeItemsResponse(OkResponse):
    """The batch without the excluded items."""

    action: Literal["exclude_items"] = "exclude_items"
    batch: BatchDetail
    # Audio objects found under the excluded items' prefixes and deleted.
    deleted_objects: NonNegativeInt
    # A leftover manifest could not be withdrawn yet (R2 failed after the exclusion was saved);
    # the next manifest publish of the batch, or the next launch, finishes it.
    withdrawal_pending: bool = False


class ListBatchesResponse(OkResponse):
    """The batches, newest first."""

    action: Literal["list_batches"] = "list_batches"
    batches: tuple[BatchSummary, ...]


class GetBatchResponse(OkResponse):
    """One batch with its items and jobs."""

    action: Literal["get_batch"] = "get_batch"
    batch: BatchDetail


class SetBatchHiddenResponse(OkResponse):
    """The batch with its new `hidden` flag."""

    action: Literal["set_batch_hidden"] = "set_batch_hidden"
    batch: BatchSummary
