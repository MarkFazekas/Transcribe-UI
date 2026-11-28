"""Constants of the download dialog (architecture §3.1 "GUI" "Download")."""

from typing import Final, Literal

from transcribe_ui.placement.classes import PlacementOutcome

# What the strategy chooser of the dialog offers: the four placement strategies and the custom plan.
type StrategyChoice = Literal["next_to_media", "mirror", "flat", "flat_with_folders", "custom"]
# How `flat_with_folders` picks the folders joined into the names: the closest ones, or chosen levels.
type FolderMode = Literal["count", "levels"]

# The strategy that places each subtitle next to its media file (needs no output folder).
NEXT_TO_MEDIA: Final[StrategyChoice] = "next_to_media"
# The strategy that joins folder names into the names (folder count or levels).
FLAT_WITH_FOLDERS: Final[StrategyChoice] = "flat_with_folders"
# The choice of an exported, edited and imported custom plan.
CUSTOM: Final[StrategyChoice] = "custom"
# The strategy chooser's entries, in order.
STRATEGY_CHOICES: Final[tuple[StrategyChoice, ...]] = (NEXT_TO_MEDIA, "mirror", "flat", FLAT_WITH_FOLDERS, CUSTOM)
# Folder mode of the closest folders (`folder_count`).
FOLDER_COUNT_MODE: Final[FolderMode] = "count"
# Folder mode of explicitly chosen levels (`folder_levels`).
FOLDER_LEVELS_MODE: Final[FolderMode] = "levels"
# How long the plan waits for the next folder count step or level tick, in milliseconds.
PLAN_DELAY_MS: Final = 250
# Largest folder count the dialog offers (a count past a file's depth keeps all of its folders).
MAX_FOLDER_COUNT: Final = 99

# The step of the step header the download dialog is (the `download` stage).
DOWNLOAD_STEP: Final = "download"

# Margins of the layout of the dialog (left, top, right, bottom), in pixels.
DIALOG_MARGINS: Final = (11, 11, 11, 11)

# Channel key of the settings load that pre-fills the dialog.
SETTINGS_KEY: Final = "settings"
# Channel key of the batch load (the step header's stage).
BATCH_KEY: Final = "batch"
# Channel key of the plan load: `plan_download`, or the `check_only` import of a custom plan.
PLAN_KEY: Final = "plan"
# Channel key of the custom plan export.
EXPORT_KEY: Final = "export"
# First part of the activity key of a batch's download (`run_download` or the custom plan's import).
ACTIVITY_KEY: Final = "download"
# Action signal the download dialog handles (`batch_id`).
OPEN_DOWNLOAD_SIGNAL: Final = "open_download"
# File name suggested for an exported custom plan (`{batch_id}`: the job's id).
PLAN_FILE_NAME: Final = "subtitle-plan-{batch_id}.json"
# Separator of the reasons in one cell.
CELL_SEPARATOR: Final = " "
# Separator of the parts of a summary line and of a level's folder names.
LIST_SEPARATOR: Final = ", "
# Outcomes the summary line counts as skipped.
SKIPPED_OUTCOMES: Final[tuple[PlacementOutcome, ...]] = (
    "skip_existing",
    "skip_folder_missing",
    "skip_empty",
    "skip_failed",
)
