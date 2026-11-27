"""The placement planner's models, the custom plan format, and the download requests and responses (§3.5).

The planner's input is its own models (a job's source path, language and subtitle state, the
scan root and the options), so it does not depend on the batch model; `plan_download` builds
them from a batch.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, NonNegativeInt, PositiveInt, model_validator

from transcribe_contracts.classes import BatchId, LanguageCode, Ulid
from transcribe_ui.api.base import FrozenModel, LocalPath
from transcribe_ui.batches.responses import BatchSummary, OkResponse
from transcribe_ui.placement.constants import CUSTOM_PLAN_VERSION, DEFAULT_FOLDER_COUNT

# How a download places its subtitles (§3.5 strategies 1-4; custom plans are imported instead);
# also what the placement settings pre-fill a download with (`api.classes.PlacementDefaults`).
type PlacementStrategy = Literal["next_to_media", "mirror", "flat", "flat_with_folders"]
# Whether a job's subtitle can be placed: it is in the cloud or in SQLite (`available`), the
# job found no speech (`empty`), the job failed, or it has no result yet (`pending`).
type SubtitleState = Literal["available", "empty", "failed", "pending"]
# What a download does with one job.
type PlacementOutcome = Literal[
    "place",
    "overwrite",
    "skip_existing",
    "skip_folder_missing",
    "skip_empty",
    "skip_failed",
    "not_finished",
]
# Why a plan cannot be carried out (the `ERROR_*` codes of `placement/constants.py`).
type PlanErrorCode = Literal[
    "name_too_long",
    "path_too_long",
    "duplicate_target",
    "target_unreadable",
    "scan_root_invalid",
    "source_path_invalid",
    "target_not_a_file",
    "outside_scan_root",
    "output_not_absolute",
    "custom_plan_schema",
    "custom_plan_duplicate_job",
    "custom_plan_unknown_job",
    "custom_plan_missing_job",
    "custom_plan_source_changed",
    "custom_plan_invalid_name",
    "custom_plan_not_srt",
    "custom_plan_outside_root",
]
# Why a download could not place one job (the `ERROR_*` codes of `placement/constants.py`).
type DownloadFailureCode = Literal["subtitle_missing", "download_write_failed"]
# Why a target name differs from the default name and the strategy's chosen folders: folder
# names were added to tell apart files of different folders, or the source extension was added
# to tell apart files of the same folder.
type RenameReason = Literal["folders", "source_extension"]


class PlacementJob(FrozenModel):
    """One job of a download: an item's subtitle in one language."""

    item_id: Ulid
    language: LanguageCode
    source_path: LocalPath = Field(description="Absolute path of the media file the subtitle belongs to.")
    subtitle: SubtitleState


class OutcomeOptions(FrozenModel):
    """Choices that decide what happens to jobs whose target exists or whose result is empty."""

    overwrite: bool = Field(default=False, description="Replace existing target files instead of skipping them.")
    include_empty: bool = Field(default=False, description="Also place empty subtitles (no speech found).")


class PlacementOptions(OutcomeOptions):
    """A placement strategy and its parameters."""

    strategy: PlacementStrategy = "next_to_media"
    output_folder: LocalPath | None = Field(
        default=None,
        description="Absolute target folder of the mirror and flat strategies; ignored by next_to_media.",
    )
    folder_count: PositiveInt = Field(
        default=DEFAULT_FOLDER_COUNT,
        description="flat_with_folders: the number of folders closest to the file joined into the name.",
    )
    folder_levels: tuple[PositiveInt, ...] = Field(
        default=(),
        description=(
            "flat_with_folders: explicit folder levels joined into the name instead of the closest ones, "
            "counted from the scan root (1 = the first folder below it); not combined with folder_count."
        ),
    )

    @model_validator(mode="after")
    def _check_output_folder(self) -> Self:
        # next_to_media ignores an output folder, so settings pre-filled with one still apply.
        if self.strategy != "next_to_media" and self.output_folder is None:
            msg = f"{self.strategy} needs an output_folder"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_folder_levels(self) -> Self:
        if self.folder_levels and self.strategy != "flat_with_folders":
            msg = "folder_levels applies only to flat_with_folders"
            raise ValueError(msg)
        if len(set(self.folder_levels)) != len(self.folder_levels):
            msg = "folder_levels lists a level more than once"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_folder_choice(self) -> Self:
        if self.folder_levels and self.folder_count != DEFAULT_FOLDER_COUNT:
            msg = "give either folder_levels or folder_count, not both"
            raise ValueError(msg)
        return self


class CustomPlacementOptions(OutcomeOptions):
    """Where an imported custom plan's relative output folders start."""

    output_folder: LocalPath = Field(description="Absolute folder every target of the custom plan must be inside.")


class PlannedPlacement(FrozenModel):
    """What a download does with one job, and where its subtitle goes."""

    item_id: Ulid
    language: LanguageCode
    source_path: LocalPath
    # None when no target could be worked out (the plan's errors say why).
    target_path: LocalPath | None
    outcome: PlacementOutcome
    renamed_by: tuple[RenameReason, ...] = ()


class PlanError(FrozenModel):
    """A problem that stops the plan from being carried out; nothing is written while one exists."""

    code: PlanErrorCode
    # Names files by their name only: messages are not path-normalised in golden files.
    message: str
    item_id: Ulid | None = None
    language: LanguageCode | None = None


class PlacementPlan(FrozenModel):
    """A validated placement: one entry per job in input order, and the problems found."""

    entries: tuple[PlannedPlacement, ...]
    errors: tuple[PlanError, ...] = ()


class CustomPlanEntry(FrozenModel):
    """One job of an exported custom plan; the user (or an LLM) edits the output fields."""

    item_id: Ulid
    language: LanguageCode
    source_path: LocalPath = Field(description="The media file; must not be changed.")
    original_folder: LocalPath = Field(description="The media file's folder; must not be changed.")
    output_folder: LocalPath = Field(
        description="Target folder, relative to the chosen output folder (empty: the output folder itself).",
    )
    output_file_name: str = Field(description="Target file name, ending with .srt.")


class CustomPlan(FrozenModel):
    """The exported custom plan: every job of the download exactly once."""

    version: Literal[1] = CUSTOM_PLAN_VERSION
    entries: tuple[CustomPlanEntry, ...]


def _check_absolute(path: str) -> str:
    # The file is read or written on this computer, so its rule decides (a relative path would
    # depend on the working folder of the frontend).
    if not Path(path).is_absolute():
        msg = "the path must be absolute"
        raise ValueError(msg)
    return path


# An absolute path of a file on this computer.
type AbsoluteFile = Annotated[LocalPath, AfterValidator(_check_absolute)]


class PlanDownloadRequest(FrozenModel):
    """Show what a download of the batch's subtitles with a strategy would do; nothing is written."""

    action: Literal["plan_download"]
    batch_id: BatchId
    placement: PlacementOptions = Field(default_factory=PlacementOptions)


class RunDownloadRequest(FrozenModel):
    """Download the batch's finished subtitles and place them with a strategy (resumable: send it again)."""

    action: Literal["run_download"]
    batch_id: BatchId
    placement: PlacementOptions = Field(default_factory=PlacementOptions)
    # Confirms replacing the existing files the plan lists (with `placement.overwrite`).
    confirm_overwrite: bool = False


class ExportCustomPlanRequest(FrozenModel):
    """Return the custom plan of the batch's jobs, pre-filled with the mirror folders and the default names."""

    action: Literal["export_custom_plan"]
    batch_id: BatchId
    # Also write the plan to this file as JSON (replaced if it exists); None returns it only.
    path: AbsoluteFile | None = None


class ImportCustomPlanRequest(FrozenModel):
    """Validate an edited custom plan file, then place the batch's finished subtitles as it says."""

    action: Literal["import_custom_plan"]
    batch_id: BatchId
    plan_path: AbsoluteFile
    placement: CustomPlacementOptions
    # Only validate and return the plan; nothing is written.
    check_only: bool = False
    # Confirms replacing the existing files the plan lists (with `placement.overwrite`).
    confirm_overwrite: bool = False


# Responses


class DownloadFailure(FrozenModel):
    """A job a download could not place; nothing is recorded for it and the other jobs went on."""

    item_id: Ulid
    language: LanguageCode
    target_path: LocalPath | None
    code: DownloadFailureCode
    # Names the file by its name only, like the plan errors.
    message: str
    # The exception type of a failed write (not the OS message: it quotes the path); None otherwise.
    reason: str | None = None


class DownloadOutcome(FrozenModel):
    """What a download did with every job of its plan."""

    # One entry per job carried out, in plan order, with the outcome as carried out: a target that
    # appeared since the plan was made is skipped as existing, a vanished original folder as missing.
    placements: tuple[PlannedPlacement, ...]
    # The jobs that could not be placed (subtitle missing in R2, write refused); sending the
    # request again tries them again.
    failures: tuple[DownloadFailure, ...]
    # Subtitle files written (placed or overwritten).
    written: NonNegativeInt
    # Jobs skipped (existing target, missing folder, empty or failed result).
    skipped: NonNegativeInt
    # Jobs without a result yet (`ready` batch); a later download places them.
    not_finished: NonNegativeInt


class PlanDownloadResponse(OkResponse):
    """The batch and the plan of placing its subtitles; nothing was written."""

    action: Literal["plan_download"] = "plan_download"
    batch: BatchSummary
    plan: PlacementPlan


class RunDownloadResponse(OkResponse):
    """The batch after the download (`downloaded` once every job has an outcome), and what was placed."""

    action: Literal["run_download"] = "run_download"
    batch: BatchSummary
    download: DownloadOutcome


class ExportCustomPlanResponse(OkResponse):
    """The batch and its custom plan, to edit and import."""

    action: Literal["export_custom_plan"] = "export_custom_plan"
    batch: BatchSummary
    plan: CustomPlan
    # The file the plan was written to, if asked.
    path: LocalPath | None


class ImportCustomPlanResponse(OkResponse):
    """The validated plan of the edited custom plan, and the download it made (None when only checked)."""

    action: Literal["import_custom_plan"] = "import_custom_plan"
    batch: BatchSummary
    plan: PlacementPlan
    download: DownloadOutcome | None
