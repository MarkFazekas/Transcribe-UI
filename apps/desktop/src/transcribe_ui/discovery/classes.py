"""Request and response models of the scan and the selection preview (architecture §3.2)."""

import re
import unicodedata
from typing import Annotated, Literal, Self

from pydantic import Field, NonNegativeInt, StringConstraints, field_validator, model_validator

from transcribe_contracts.classes import LanguageCode, MediaKind, Ulid
from transcribe_contracts.constants import WHISPER_LANGUAGES
from transcribe_ui.api.base import FrozenModel, LocalPath, PathFlavor
from transcribe_ui.discovery.constants import (
    DEFAULT_AUDIO_EXTENSIONS,
    DEFAULT_VIDEO_EXTENSIONS,
    EXTENSION_PATTERN,
    NAME_FILTER_MAX_LENGTH,
    REGEX_MAX_LENGTH,
    SIDECAR_EXTENSIONS,
)

# A media file extension: lower case, without the dot.
type FileExtension = Annotated[str, StringConstraints(pattern=EXTENSION_PATTERN)]
# Format of a sidecar subtitle file (its extension, lower case).
type SubtitleFormat = Literal["ass", "srt", "ssa", "sub", "vtt"]
# Why an entry was left out of a scan, or why a selection path matched nothing.
type ScanWarningCode = Literal["invalid_name", "link_skipped", "unreadable", "too_deep", "no_media_at_path"]
# How a subtitle filter combines its languages: has all, has at least one, lacks at least one.
type SubtitleRule = Literal["all", "any", "missing_one"]
# Check state of a folder with selected files: every media file below it, or only some.
type FolderState = Literal["all", "partial"]
# How a name filter compares the file name without its extension (case-insensitive).
type NameMatch = Literal["starts_with", "ends_with", "contains", "regex"]


# Requests


class ScanSpec(FrozenModel):
    """What to scan and how to classify it; the GUI fills the defaults from the settings."""

    root: LocalPath = Field(description="Absolute path of the folder to scan.")
    video_extensions: tuple[FileExtension, ...] = DEFAULT_VIDEO_EXTENSIONS
    audio_extensions: tuple[FileExtension, ...] = DEFAULT_AUDIO_EXTENSIONS
    include_hidden: bool = Field(default=False, description="Also scan hidden and system files and folders.")

    @model_validator(mode="after")
    def _check_extensions(self) -> Self:
        video = set(self.video_extensions)
        audio = set(self.audio_extensions)
        both = sorted(video & audio)
        if both:
            msg = f"extensions listed as both video and audio: {both}"
            raise ValueError(msg)
        sidecars = sorted((video | audio) & set(SIDECAR_EXTENSIONS))
        if sidecars:
            msg = f"subtitle extensions cannot be media extensions: {sidecars}"
            raise ValueError(msg)
        return self


class ScanTreeRequest(ScanSpec):
    """List the media files below a folder with their sidecar subtitles; changes nothing."""

    action: Literal["scan_tree"]


class SubtitleFilter(FrozenModel):
    """Match files by the languages of their (not forced) sidecar subtitles."""

    languages: tuple[LanguageCode, ...] = Field(min_length=1)
    rule: SubtitleRule

    @field_validator("languages")
    @classmethod
    def _check_languages(cls, languages: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(languages) - set(WHISPER_LANGUAGES))
        if unknown:
            msg = f"not Whisper languages: {unknown}"
            raise ValueError(msg)
        return languages


class NameFilter(FrozenModel):
    """Match files by their name without the extension, ignoring case."""

    match: NameMatch
    text: str = Field(min_length=1, max_length=NAME_FILTER_MAX_LENGTH)

    @model_validator(mode="after")
    def _check_regex(self) -> Self:
        if self.match == "regex":
            if len(self.text) > REGEX_MAX_LENGTH:
                msg = f"a regular expression may have at most {REGEX_MAX_LENGTH} characters"
                raise ValueError(msg)
            try:
                re.compile(unicodedata.normalize("NFC", self.text))
            except re.error as error:
                msg = f"not a valid regular expression: {error.msg}"
                raise ValueError(msg) from error
        return self


class SelectionFilter(FrozenModel):
    """The filters active when an operation was made; every given part must match."""

    media_kind: Literal["any", "audio", "video"] = "any"
    subtitles: SubtitleFilter | None = None
    name: NameFilter | None = None


class SelectionOperation(FrozenModel):
    """Include or exclude the matching media files at or below a path (a folder or a file)."""

    operation: Literal["include", "exclude"]
    path: LocalPath
    filters: SelectionFilter = SelectionFilter()


class PreviewSelectionRequest(FrozenModel):
    """Scan, then apply the selection operations in order; changes nothing."""

    action: Literal["preview_selection"]
    scan: ScanSpec
    scan_id: Ulid | None = Field(
        default=None,
        description="Reuse this scan of the same options if this process still keeps it; else scan again.",
    )
    operations: tuple[SelectionOperation, ...] = ()


# Responses


class Sidecar(FrozenModel):
    """A subtitle file next to a media file."""

    name: str
    path: LocalPath
    # Its extension, lower case.
    extension: SubtitleFormat
    # None when the name gives no recognisable language ("unknown language").
    language: str | None
    forced: bool


class ScanFolder(FrozenModel):
    """A folder holding media files at some depth; index 0 is the scan root."""

    name: str
    path: LocalPath
    # Index of the parent in `folders`; None for the scan root.
    parent: NonNegativeInt | None


class ScanFile(FrozenModel):
    """A media file found by the scan."""

    name: str
    path: LocalPath
    # Index of its folder in `folders`.
    folder: NonNegativeInt
    kind: MediaKind
    size_bytes: NonNegativeInt
    mtime_ns: int
    subtitles: tuple[Sidecar, ...]


class ScanWarning(FrozenModel):
    """An entry left out of the scan, or a selection path that matched nothing."""

    code: ScanWarningCode
    path: LocalPath
    message: str


class ScanCounts(FrozenModel):
    """Totals of a scan."""

    folders: NonNegativeInt
    video_files: NonNegativeInt
    audio_files: NonNegativeInt
    subtitle_files: NonNegativeInt


class ScanTreeResponse(FrozenModel):
    """The media tree, flattened: folders in depth-first order, files grouped by folder."""

    status: Literal["ok"] = "ok"
    action: Literal["scan_tree"] = "scan_tree"
    # Id under which this process keeps the scan for `preview_selection`.
    scan_id: Ulid
    root: LocalPath
    path_flavor: PathFlavor
    folders: tuple[ScanFolder, ...]
    files: tuple[ScanFile, ...]
    counts: ScanCounts
    warnings: tuple[ScanWarning, ...]


class SelectedFile(FrozenModel):
    """A selected media file and the subtitles it already has."""

    name: str
    path: LocalPath
    kind: MediaKind
    size_bytes: NonNegativeInt
    # Languages of its sidecar subtitles that are not forced, sorted.
    subtitle_languages: tuple[str, ...]
    # Languages of its forced sidecar subtitles, sorted.
    forced_subtitle_languages: tuple[str, ...]
    # Sidecar subtitles without a recognisable language.
    unknown_language_subtitles: NonNegativeInt


class FolderSelection(FrozenModel):
    """Check state of a folder with at least one selected media file below it."""

    path: LocalPath
    state: FolderState


class SelectionCounts(FrozenModel):
    """Totals of a selection."""

    files: NonNegativeInt
    video_files: NonNegativeInt
    audio_files: NonNegativeInt
    size_bytes: NonNegativeInt


class PreviewSelectionResponse(FrozenModel):
    """The selected files in scan order, the folders' check states and the totals."""

    status: Literal["ok"] = "ok"
    action: Literal["preview_selection"] = "preview_selection"
    # The scan the selection was resolved on (the kept one, or a new one).
    scan_id: Ulid
    root: LocalPath
    path_flavor: PathFlavor
    files: tuple[SelectedFile, ...]
    folders: tuple[FolderSelection, ...]
    counts: SelectionCounts
    warnings: tuple[ScanWarning, ...]
