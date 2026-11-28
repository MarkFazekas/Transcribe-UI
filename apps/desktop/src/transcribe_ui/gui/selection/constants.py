"""Constants of the New job page: folder, tree, filters, selection and languages (architecture §3.1 "GUI")."""

from typing import Final, Literal

# How a row of the tree is ticked: every media file at or below it selected, some of them, or none.
type CheckMark = Literal["checked", "partial", "unchecked"]
# Languages (Whisper codes), in order.
type Languages = tuple[str, ...]
# Where the scan of the chosen folder is.
type ScanStatus = Literal["idle", "scanning", "stopped", "failed", "ready"]
# The media kind filter's choice.
type KindChoice = Literal["any", "audio", "video"]

# A row ticked: every media file at or below it is selected.
CHECKED: Final[CheckMark] = "checked"
# A folder with only some of the media files below it selected.
PARTIAL: Final[CheckMark] = "partial"
# A row with nothing selected at or below it.
UNCHECKED: Final[CheckMark] = "unchecked"

# Channel key of the folder scans (a new folder replaces the scan in flight).
SCAN_KEY: Final = "new_job_scan"
# Channel key of the selection previews, sent on every tick (the latest wins).
PREVIEW_KEY: Final = "new_job_selection"
# Channel key of the previews of what the active filters match.
MATCH_KEY: Final = "new_job_matches"
# Channel key of the settings loads (scan options and the enabled languages).
SETTINGS_KEY: Final = "new_job_settings"
# Channel key of the load of a job whose selection is edited.
EDIT_KEY: Final = "new_job_edit"
# Channel key of creating or saving the job (one at a time).
SAVE_KEY: Final = "new_job_save"

# Signal that opens a job page (`batch_id`).
OPEN_JOB_SIGNAL: Final = "open_job"
# Signal that tells the pages that batches changed (a tuple of batch ids).
BATCHES_CHANGED_SIGNAL: Final = "batches_changed"

# Column of the tree with the name and the tick box.
NAME_COLUMN: Final = 0
# Column of the subtitles a file already has.
SUBTITLES_COLUMN: Final = 1
# Column of the media kind.
KIND_COLUMN: Final = 2
# Column of the file size.
SIZE_COLUMN: Final = 3
# Column of the languages a selected file is transcribed into.
TARGETS_COLUMN: Final = 4
# Number of columns of the tree.
COLUMN_COUNT: Final = 5

# Separator of the languages in one cell.
LANGUAGE_SEPARATOR: Final = ", "
# Bytes in one step of the size units (decimal units: kB, MB, GB, TB).
BYTES_PER_UNIT_STEP: Final = 1000
# Wait after the last keystroke in the name filter before it applies, in milliseconds.
NAME_FILTER_DELAY_MS: Final = 300
