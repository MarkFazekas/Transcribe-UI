"""Inputs and results of the placement planner, and the custom plan format (architecture §3.5).

The planner's input is its own models (a job's source path, language and subtitle state, the
scan root and the options), so it does not depend on the batch model; `plan_download` builds
them from a batch.
"""

from typing import Literal, Self

from pydantic import Field, PositiveInt, model_validator

from transcribe_contracts.classes import LanguageCode, Ulid
from transcribe_ui.api.base import FrozenModel, LocalPath
from transcribe_ui.placement.constants import CUSTOM_PLAN_VERSION

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
        description="Absolute target folder of the mirror and flat strategies.",
    )
    folder_count: PositiveInt = Field(
        default=1,
        description="flat_with_folders: the number of folders closest to the file joined into the name.",
    )
    folder_levels: tuple[PositiveInt, ...] = Field(
        default=(),
        description=(
            "flat_with_folders: explicit folder levels joined into the name instead of the closest ones, "
            "counted from the scan root (1 = the first folder below it)."
        ),
    )

    @model_validator(mode="after")
    def _check_output_folder(self) -> Self:
        if self.strategy == "next_to_media" and self.output_folder is not None:
            msg = "next_to_media takes no output_folder"
            raise ValueError(msg)
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

    code: str
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
