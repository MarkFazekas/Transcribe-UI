"""Pydantic models of the JSON objects stored in R2 (architecture §4).

Compatibility rule: a minor schema version may only add optional fields and new values of
open sets (such as language codes). Readers therefore ignore unknown fields and accept any
well-formed language code; the Whisper language set is enforced where requests are created.
Use `transcribe_contracts.reading` to read stored objects: it refuses another major version
before validating and checks that the object matches its key.
"""

import re
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, NoReturn, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

from transcribe_contracts.constants import (
    AUDIO_EXTENSION_PATTERN,
    BATCH_ID_PATTERN,
    LANGUAGE_CODE_PATTERN,
    SCHEMA_MAJOR,
    SCHEMA_VERSION,
    SCHEMA_VERSION_PATTERN,
    ULID_PATTERN,
)
from transcribe_contracts.layout import audio_key


def _fail(message: str) -> NoReturn:
    raise ValueError(message)


def schema_major(version: str) -> int:
    """Return the major part of a schema version string.

    Raises:
        ValueError: If the string is not '<major>.<minor>'.
    """
    match = re.fullmatch(SCHEMA_VERSION_PATTERN, version)
    if match is None:
        _fail(f"schema version {version!r} is not '<major>.<minor>'")
    return int(match.group(1))


def _check_schema_version(version: str) -> str:
    if schema_major(version) != SCHEMA_MAJOR:
        _fail(f"schema version {version!r} is not supported (supported major: {SCHEMA_MAJOR})")
    return version


type SchemaVersion = Annotated[str, AfterValidator(_check_schema_version)]
type BatchId = Annotated[str, StringConstraints(pattern=BATCH_ID_PATTERN)]
type Ulid = Annotated[str, StringConstraints(pattern=ULID_PATTERN)]
type LanguageCode = Annotated[str, StringConstraints(pattern=LANGUAGE_CODE_PATTERN)]
type AudioExtension = Annotated[str, StringConstraints(pattern=AUDIO_EXTENSION_PATTERN)]
type NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
type MediaKind = Literal["audio", "video"]
type PathFlavor = Literal["windows", "posix"]
type TranscriptionMode = Literal["normal", "careful"]
type JobStatus = Literal["ok", "no_speech", "failed"]
type RunState = Literal["starting", "transcribing", "finished", "failed"]


class ContractModel(BaseModel):
    """Base of every contract model: immutable, unknown fields ignored (see module docstring)."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class AudioObject(ContractModel):
    """One uploaded audio object of an item."""

    object_key: str
    track_index: NonNegativeInt | None = Field(
        description="Audio track of the source video; None for an audio file uploaded as-is.",
    )
    extension: AudioExtension
    codec: NonEmptyText
    duration_s: NonNegativeFloat
    size_bytes: PositiveInt


class TranscribeJob(ContractModel):
    """One transcription of one item into one language."""

    language: LanguageCode
    mode: TranscriptionMode
    audio_object_key: str


class ManifestItem(ContractModel):
    """One source media file of a batch."""

    item_id: Ulid
    original_path: NonEmptyText
    source_size: NonNegativeInt
    source_mtime_ns: int
    media_kind: MediaKind
    audio: tuple[AudioObject, ...] = Field(min_length=1)
    jobs: tuple[TranscribeJob, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_audio(self) -> Self:
        keys = {audio.object_key for audio in self.audio}
        if len(keys) != len(self.audio):
            _fail(f"item {self.item_id} lists the same audio object twice")
        tracks = [audio.track_index for audio in self.audio]
        if self.media_kind == "audio" and tracks != [None]:
            _fail(f"audio item {self.item_id} must have exactly one audio object without a track")
        distinct_tracks = len(set(tracks)) == len(tracks)
        distinct_tracks = distinct_tracks and None not in tracks
        if self.media_kind == "video" and not distinct_tracks:
            _fail(f"video item {self.item_id} needs one audio object per distinct track")
        return self

    @model_validator(mode="after")
    def _check_jobs(self) -> Self:
        languages = [job.language for job in self.jobs]
        if len(set(languages)) != len(languages):
            _fail(f"item {self.item_id} has more than one job for the same language")
        listed = {audio.object_key for audio in self.audio}
        unknown = {job.audio_object_key for job in self.jobs} - listed
        if unknown:
            _fail(f"item {self.item_id} has jobs for audio objects it does not list: {sorted(unknown)}")
        return self


def _pure_path(path: str, flavor: PathFlavor) -> PurePath:
    return PureWindowsPath(path) if flavor == "windows" else PurePosixPath(path)


class Manifest(ContractModel):
    """Batch manifest: `batch_metadata/<batch_id>.json`, written by the desktop app last."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    batch_id: BatchId
    created_at: AwareDatetime
    scan_root: NonEmptyText
    path_flavor: PathFlavor
    items: tuple[ManifestItem, ...] = Field(min_length=1)  # noqa: WPS110 - JSON field name fixed by the contract

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        self._check_items()
        self._check_paths()
        return self

    def _check_items(self) -> None:
        item_ids = [manifest_item.item_id for manifest_item in self.items]
        if len(set(item_ids)) != len(item_ids):
            _fail("item ids are not unique")
        for manifest_item in self.items:
            for audio in manifest_item.audio:
                expected = audio_key(self.batch_id, manifest_item.item_id, audio.extension, audio.track_index)
                if audio.object_key != expected:
                    _fail(f"audio object key {audio.object_key!r} must be {expected!r}")

    def _check_paths(self) -> None:
        root = _pure_path(self.scan_root, self.path_flavor)
        if not root.is_absolute():
            _fail(f"scan_root {self.scan_root!r} is not an absolute {self.path_flavor} path")
        for manifest_item in self.items:
            if not _pure_path(manifest_item.original_path, self.path_flavor).is_relative_to(root):
                _fail(f"{manifest_item.original_path!r} is not under scan_root {self.scan_root!r}")


class EngineInfo(ContractModel):
    """Transcription engine settings recorded with every result."""

    name: NonEmptyText
    version: NonEmptyText
    model: NonEmptyText
    compute_type: NonEmptyText
    batch_size: PositiveInt


class JobResult(ContractModel):
    """Job result `<batch_id>/results/<ulid>.<lang>.json`; its presence marks the job done."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    batch_id: BatchId
    item_id: Ulid
    language: LanguageCode
    mode: TranscriptionMode
    run_id: Ulid
    status: JobStatus
    error: NonEmptyText | None = None
    aligned: bool
    cue_count: NonNegativeInt
    audio_duration_s: NonNegativeFloat
    started_at: AwareDatetime
    finished_at: AwareDatetime
    engine: EngineInfo

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        if (self.status == "failed") != (self.error is not None):
            _fail("error must be set exactly when status is 'failed'")
        if (self.status == "ok") != (self.cue_count > 0):
            _fail("cue_count must be positive exactly when status is 'ok'")
        if self.finished_at < self.started_at:
            _fail("finished_at is before started_at")
        return self


class JobCounts(ContractModel):
    """Number of jobs per status."""

    ok: NonNegativeInt
    no_speech: NonNegativeInt
    failed: NonNegativeInt


class BatchSummary(ContractModel):
    """Batch summary `<batch_id>/_done.json`, written by the worker after the batch's last job."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    batch_id: BatchId
    run_id: Ulid
    finished_at: AwareDatetime
    counts: JobCounts


class JobRef(ContractModel):
    """Reference to one job of one batch."""

    batch_id: BatchId
    item_id: Ulid
    language: LanguageCode


class RunStatus(ContractModel):
    """Heartbeat and progress `runs/<run_id>/status.json`, overwritten by the worker."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    run_id: Ulid
    pod_id: NonEmptyText
    state: RunState
    started_at: AwareDatetime
    updated_at: AwareDatetime
    deadline_at: AwareDatetime
    current: JobRef | None = None
    done_jobs: NonNegativeInt
    total_jobs: NonNegativeInt
    error: NonEmptyText | None = None

    @model_validator(mode="after")
    def _check_progress(self) -> Self:
        if self.done_jobs > self.total_jobs:
            _fail("done_jobs exceeds total_jobs")
        if self.updated_at < self.started_at:
            _fail("updated_at is before started_at")
        if self.deadline_at <= self.started_at:
            _fail("deadline_at must be after started_at")
        if (self.state == "failed") != (self.error is not None):
            _fail("error must be set exactly when state is 'failed'")
        if self.state in {"finished", "failed"} and self.current is not None:
            _fail(f"a {self.state!r} run has no current job")
        return self
