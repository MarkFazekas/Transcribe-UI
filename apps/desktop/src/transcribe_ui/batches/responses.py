"""Response models of the batches and the review step (architecture §3.3)."""

from typing import Literal

from pydantic import AwareDatetime, NonNegativeInt

from transcribe_contracts.classes import BatchId, MediaKind, TranscriptionMode, Ulid
from transcribe_ui.api.base import FrozenModel, LocalPath, PathFlavor
from transcribe_ui.batches.payloads import ItemPhase, ItemProblem, JobKind
from transcribe_ui.discovery.classes import ScanWarning
from transcribe_ui.media.classes import AudioTrack, SubtitleStream
from transcribe_ui.state.models import BatchPhase


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
    # Only `extract` jobs: the batch needs no upload and no cloud run.
    extract_only: bool


class BatchDetail(FrozenModel):
    """A batch with its items and jobs."""

    summary: BatchSummary
    scan_root: LocalPath
    path_flavor: PathFlavor
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
