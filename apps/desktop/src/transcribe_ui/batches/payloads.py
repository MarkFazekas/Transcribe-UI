"""Pydantic payloads of the batch tables (the JSON columns of `state/models.py`), and their phases and kinds."""

from typing import Literal

from pydantic import NonNegativeInt

from transcribe_contracts.classes import MediaKind, TranscriptionMode
from transcribe_ui.api.base import FrozenModel
from transcribe_ui.media.classes import ProbeResult

# Phase of an item: `composing` and `reviewing` follow the batch; `reviewed` is probed with every
# job resolved; `uploaded` has its audio verified and its extractions stored (written by the upload).
type ItemPhase = Literal["composing", "reviewing", "reviewed", "uploaded"]
# What a job does: transcribe an audio track in the cloud, or extract an embedded text subtitle locally.
type JobKind = Literal["transcribe", "extract"]
# Why an item cannot be resolved until it is probed again (or excluded).
type ProblemCode = Literal["source_missing", "probe_failed", "source_changing"]


class BatchPayload(FrozenModel):
    """What a batch row keeps besides its phase and flags."""

    scan_root: str
    path_flavor: Literal["windows", "posix"]
    # The batch's default target languages, as requested.
    languages: tuple[str, ...]


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


class JobPayload(FrozenModel):
    """What a job row keeps besides its language, kind and status."""

    mode: TranscriptionMode
    # Audio track of a video (`transcribe`); None for an audio file (uploaded as it is) or not chosen yet.
    track_index: NonNegativeInt | None = None
    # Embedded subtitle stream (`extract`).
    stream_index: NonNegativeInt | None = None
