"""Pydantic payloads of the batch tables (the JSON columns of `state/models.py`), and their phases and kinds."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue, NonNegativeInt

from transcribe_contracts.classes import BatchId, MediaKind, TranscriptionMode, Ulid
from transcribe_ui.api.base import FrozenModel
from transcribe_ui.media.classes import ProbeResult
from transcribe_ui.upload.payloads import CopySource

# Phase of an item: `composing` and `reviewing` follow the batch; `reviewed` is probed with every
# job resolved; `uploaded` has its audio verified and its extractions stored (written by the upload).
type ItemPhase = Literal["composing", "reviewing", "reviewed", "uploaded"]
# What a job does: transcribe an audio track in the cloud, or extract an embedded text subtitle locally.
type JobKind = Literal["transcribe", "extract"]
# What a cloud cleanup deletes: the audio only (results stay downloadable), or every object of the batch.
type CleanupScope = Literal["audio", "everything"]
# Why an item cannot be resolved until it is probed again (or excluded).
type ProblemCode = Literal["source_missing", "probe_failed", "source_changing"]
# Why a transcribe job failed when the desktop decided it (not the worker): its result could not
# be read, or there was none, once the batch's `_done.json` was there.
type ResultErrorCode = Literal["result_unreadable", "result_missing"]


class RetryJob(FrozenModel):
    """A transcribe job of a batch, named by its item and language."""

    item_id: Ulid
    language: str


class RetryOrigin(FrozenModel):
    """What a retry batch retries: its source batch and the source's jobs (sorted), plan m5 §1 "Retry"."""

    source_batch_id: BatchId
    jobs: tuple[RetryJob, ...]


class StoredItemLanguages(BaseModel):
    """Other target languages of one file, as stored (plain values, unknown keys ignored)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    path: str
    languages: tuple[str, ...]


class StoredComposition(BaseModel):
    """The selection a batch was composed from (`create_batch`, `edit_batch`), kept so it can be reopened.

    The request's fields without `scan_id` (a scan this process kept) and the confirmation flag,
    as plain stored values: the scan options and the operations as their JSON, languages as
    strings, unknown keys ignored, so a later change of the request models never makes a stored
    batch unreadable. `BatchDetail.composition` maps it to the request models when it still fits.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    scan: dict[str, JsonValue]
    operations: tuple[dict[str, JsonValue], ...]
    languages: tuple[str, ...]
    item_languages: tuple[StoredItemLanguages, ...] = ()


class AdoptedBatch(FrozenModel):
    """Another computer's batch recorded from its manifest (`adopt_cloud_batch`, architecture §3.9)."""

    # The uploading installation's id from the manifest (`uploaded_by`), None when it had none.
    uploaded_by: str | None = None


class BatchPayload(FrozenModel):
    """What a batch row keeps besides its phase and flags."""

    scan_root: str
    path_flavor: Literal["windows", "posix"]
    # The batch's default target languages, as requested.
    languages: tuple[str, ...]
    # The selection of the last `create_batch` or `edit_batch`; None for a retry batch and for a
    # batch stored before the composition was kept (the Alembic revision `composition_and_run_report`).
    composition: StoredComposition | None = None
    # Set on a retry batch (`create_retry_batch`): one retry batch per source and job set.
    retry_of: RetryOrigin | None = None
    # A cloud cleanup of this scope was recorded and has not finished its deletes yet; no worker
    # may start until it is repeated (`cleanup_cloud`).
    cleanup_pending: CleanupScope | None = None
    # Set on a batch of another computer, recorded from its manifest (`adopt_cloud_batch`); None for
    # a batch composed and uploaded here.
    adopted: AdoptedBatch | None = None


class PendingWithdrawal(FrozenModel):
    """A leftover manifest of the batch to withdraw from R2, and the items whose results go with it."""

    # SHA-256 (hex) of the manifest as this database built it: only that object is withdrawn.
    manifest_sha256: str
    # Items excluded or sent back to review since: their results are deleted, the others' kept.
    item_ids: tuple[str, ...]


class ItemProblem(FrozenModel):
    """Why an item cannot be resolved: it must be probed again or excluded."""

    code: ProblemCode
    message: str


class ItemPayload(FrozenModel):
    """What an item row keeps besides its phase."""

    # Position in the selection (scan order); items are listed in this order.
    position: NonNegativeInt
    path: str
    # Path below the scan root, `/`-separated (the same on every OS).
    relative_path: str
    name: str
    kind: MediaKind
    # Size and mtime of the file when it was selected, then when it was last probed.
    size_bytes: NonNegativeInt
    mtime_ns: int
    # Languages of its sidecar subtitles (not forced) when it was selected.
    subtitle_languages: tuple[str, ...]
    probe: ProbeResult | None = None
    problem: ItemProblem | None = None
    # A retry item's audio, copied server side from the source batch instead of read from the file
    # (empty for every other item; such items skip the source-unchanged check, plan m5 §1 "Retry").
    copy_sources: tuple[CopySource, ...] = ()


class JobPayload(FrozenModel):
    """What a job row keeps besides its language, kind and status."""

    mode: TranscriptionMode
    # Audio track of a video (`transcribe`); None for an audio file (uploaded as it is) or not chosen yet.
    track_index: NonNegativeInt | None = None
    # Embedded subtitle stream (`extract`).
    stream_index: NonNegativeInt | None = None
    # Why a transcribe job failed: its result's `error`, or `unreadable result` when its result
    # JSON could not be read once the batch's `_done.json` was there (`refresh_status`).
    result_error: str | None = None
    # The code of a failure the desktop decided (`result_error` is then its English text); None
    # for the worker's own errors, which are not translated.
    result_error_code: ResultErrorCode | None = None
    # The run that wrote the job's result and when it worked on the job (the result JSON's
    # `run_id`, `started_at`, `finished_at`, on the worker's clock); None without a readable result.
    # The remaining-time estimate measures the worker's pace from them (`runs/remaining.py`).
    result_run_id: str | None = None
    result_started_at: AwareDatetime | None = None
    result_finished_at: AwareDatetime | None = None
