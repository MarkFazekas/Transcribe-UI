"""Request and response models of the backend façade (architecture §3.1).

Every frontend builds a `Request`, hands it to `Backend.execute` and renders the `Response` and
the `ProgressEvent`s. A response is one of: the request's own success model (`status` "ok"),
`NeedsConfirmation` (nothing was changed; send the request again with the confirmation field
set) or `ErrorResponse`.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, JsonValue, NonNegativeInt, PositiveInt, StringConstraints, model_validator

from transcribe_contracts.constants import WHISPER_LANGUAGES
from transcribe_ui.api.base import FrozenModel, LocalPath
from transcribe_ui.discovery.classes import (
    FileExtension,
    PreviewSelectionRequest,
    PreviewSelectionResponse,
    ScanTreeRequest,
    ScanTreeResponse,
)
from transcribe_ui.discovery.constants import SIDECAR_EXTENSIONS
from transcribe_ui.placement.classes import PlacementStrategy


def _check_whisper_language(code: str) -> str:
    if code not in WHISPER_LANGUAGES:
        msg = f"{code!r} is not a language Whisper can transcribe"
        raise ValueError(msg)
    return code


def _check_absolute(path: str) -> str:
    if not (PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()):
        msg = "the path must be absolute"
        raise ValueError(msg)
    return path


def _check_unique[ItemT](entries: tuple[ItemT, ...]) -> tuple[ItemT, ...]:
    if len(set(entries)) != len(entries):
        msg = "every entry must appear only once"
        raise ValueError(msg)
    return entries


# A language code of the Whisper language set (machine-independent, so every frontend agrees).
type WhisperLanguage = Annotated[str, AfterValidator(_check_whisper_language)]
# Distinct media file extensions (the scan's extension rule, §3.2).
type MediaExtensions = Annotated[tuple[FileExtension, ...], AfterValidator(_check_unique)]
# A non-empty path on this computer.
type SetPath = Annotated[LocalPath, StringConstraints(min_length=1)]
# An absolute path of an executable: absolute in the Windows or the POSIX sense, so the rule
# does not depend on the machine; the tool lookup also refuses one that is not absolute here.
type ToolPath = Annotated[SetPath, AfterValidator(_check_absolute)]


# Requests


class GetAppInfoRequest(FrozenModel):
    """Ask for the app version and the data contract version (nothing machine-specific)."""

    # Request actions have no default, so the schema marks them required.
    action: Literal["get_app_info"]


# Settings (stored in the user config dir; they only pre-fill request fields, plan m5 §1).
# Every field is required, so `update_settings` always carries the whole document and a partial
# one is refused instead of silently resetting the missing fields; the defaults are
# `state.settings.default_settings()`.


class PlacementDefaults(FrozenModel):
    """Placement choices a download request is pre-filled with (§3.5)."""

    strategy: PlacementStrategy
    # Target folder of the mirror and flat strategies; None until the user picks one.
    output_folder: SetPath | None
    # Folders closest to the file joined into the name by `flat_with_folders` (X).
    folder_count: PositiveInt
    overwrite: bool


class Settings(FrozenModel):
    """The user's settings (all fields required; see `state.settings.default_settings`)."""

    # Languages the GUI offers in choosers and filters, in the user's order.
    enabled_languages: Annotated[tuple[WhisperLanguage, ...], Field(min_length=1), AfterValidator(_check_unique)]
    video_extensions: MediaExtensions
    audio_extensions: MediaExtensions
    # Whether scans include hidden and system files and folders.
    include_hidden: bool
    # ffmpeg/ffprobe to use instead of the bundled build (§3.4); None uses the bundled one.
    ffmpeg_path: ToolPath | None
    ffprobe_path: ToolPath | None
    placement: PlacementDefaults

    @model_validator(mode="after")
    def _check_extensions(self) -> "Settings":
        # The same rules as the scan's (`ScanSpec`), so saved settings always make a valid scan.
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


class LanguageOption(FrozenModel):
    """A Whisper language as the settings page shows it."""

    code: str
    name: str
    enabled: bool
    # Whether WhisperX has a default alignment model (word-level timing) for it.
    alignment: bool


class GetSettingsRequest(FrozenModel):
    """Ask for the stored settings and every Whisper language (needs no database)."""

    action: Literal["get_settings"]


class UpdateSettingsRequest(FrozenModel):
    """Replace the stored settings with these (needs no database; the last writer wins)."""

    action: Literal["update_settings"]
    settings: Settings


# Discriminated on `action`; each part adds its request models here (`A | B | ...`).
type Request = Annotated[
    GetAppInfoRequest | ScanTreeRequest | PreviewSelectionRequest | GetSettingsRequest | UpdateSettingsRequest,
    Field(discriminator="action"),
]


# Responses


class GetAppInfoResponse(FrozenModel):
    """Versions of the running app."""

    status: Literal["ok"] = "ok"
    action: Literal["get_app_info"] = "get_app_info"
    app_version: str
    contracts_schema_version: str


class GetSettingsResponse(FrozenModel):
    """The stored settings and every Whisper language, sorted by code."""

    status: Literal["ok"] = "ok"
    action: Literal["get_settings"] = "get_settings"
    settings: Settings
    languages: tuple[LanguageOption, ...]


class UpdateSettingsResponse(FrozenModel):
    """The settings as stored now, and every Whisper language, sorted by code."""

    status: Literal["ok"] = "ok"
    action: Literal["update_settings"] = "update_settings"
    settings: Settings
    languages: tuple[LanguageOption, ...]


class ErrorBody(FrozenModel):
    """What went wrong: a stable code, a human-readable message and structured details."""

    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ErrorResponse(FrozenModel):
    """The request failed; for most codes nothing was changed (see the code's documentation)."""

    status: Literal["error"] = "error"
    # None when the request document could not be read or validated.
    action: str | None
    error: ErrorBody


class Confirmation(FrozenModel):
    """One thing the user has to confirm before the request is carried out."""

    # Request field that confirms it when set.
    field: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class NeedsConfirmation(FrozenModel):
    """Nothing was changed: send the same request again with the listed fields set."""

    status: Literal["needs_confirmation"] = "needs_confirmation"
    action: str
    confirmations: tuple[Confirmation, ...] = Field(min_length=1)


# Discriminated on `action`; each part adds its success response models here.
type SuccessResponse = Annotated[
    GetAppInfoResponse | ScanTreeResponse | PreviewSelectionResponse | GetSettingsResponse | UpdateSettingsResponse,
    Field(discriminator="action"),
]
# Every response of `Backend.execute`, discriminated on `status`.
type Response = Annotated[SuccessResponse | ErrorResponse | NeedsConfirmation, Field(discriminator="status")]


class ProgressEvent(FrozenModel):
    """Progress of a long request; the CLI writes these to stderr as NDJSON."""

    action: str
    stage: str
    completed: NonNegativeInt
    total: NonNegativeInt | None = None
    path: LocalPath | None = None
