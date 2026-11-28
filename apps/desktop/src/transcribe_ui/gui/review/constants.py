"""Constants of the review and upload step (architecture §3.1 "GUI" "Review and upload")."""

from typing import Final, Literal

# Where the review views of the page's job are: being checked (`review_batch` in flight),
# stopped by the user, waiting for the job's upload to stop, or shown.
type ReviewState = Literal["checking", "stopped", "waiting_upload", "shown"]
# Which part of the review a table shows: embedded subtitles, audio tracks, or the files that
# need attention.
type ReviewTab = Literal["subtitles", "tracks", "attention"]

# Channel key of the page's `get_batch` loads (the latest wins).
DETAIL_KEY: Final = "review_detail"
# The step of the step header the review page shows.
REVIEW_STEP: Final = "review_upload"
# The job's stage after the upload, whose next step is on the Cloud page.
TRANSCRIPTION_STAGE: Final = "transcription"
# The job's stages past the transcription, whose next step is on the job page (Download).
LATER_STAGES: Final = frozenset(("download", "done"))
# The review tables in the order of their tabs.
TABS: Final[tuple[ReviewTab, ...]] = ("subtitles", "tracks", "attention")
# Signal that tells the pages that batches changed (a tuple of batch ids).
BATCHES_CHANGED_SIGNAL: Final = "batches_changed"
# Signal that opens a job page (`batch_id`).
OPEN_JOB_SIGNAL: Final = "open_job"
# Signal that opens the Cloud page (start transcription).
OPEN_CLOUD_SIGNAL: Final = "open_cloud"
# Allowed action (`BatchSummary.actions`) of the upload.
UPLOAD_ACTION: Final = "run_upload"
# Allowed action of choosing in the review (`resolve_review`).
RESOLVE_ACTION: Final = "resolve_review"
# Allowed action of removing files from the job.
EXCLUDE_ACTION: Final = "exclude_items"
# Allowed action of checking the files in review.
REVIEW_ACTION: Final = "review_batch"
# Outcome of `run_upload` that leaves files in review.
ITEMS_IN_REVIEW_OUTCOME: Final = "items_in_review"
# Stage of the progress event the upload sends after each file.
ITEM_STAGE: Final = "upload_item"
# Stage of the progress event the upload sends after each part of an object.
BYTES_STAGE: Final = "upload_bytes"
# Subtitle stream kind that can be extracted as text (the others are transcribed only).
TEXT_SUBTITLE: Final = "text"
# Media kind uploaded as it is, without a track choice.
AUDIO_KIND: Final = "audio"
# Path flavour whose paths are Windows paths.
WINDOWS_FLAVOR: Final = "windows"
# Separator of names and languages in one text.
LIST_SEPARATOR: Final = ", "
# Most file names an upload outcome lists before "and N more".
NAMES_SHOWN: Final = 5
# Percent of a whole (the progress bar's range).
PERCENT: Final = 100

# Column of the review tables' Remove tick box.
TICK_COLUMN: Final = 0
# Column of the file's name.
FILE_COLUMN: Final = 1
# Column of the file's folder below the job's folder.
FOLDER_COLUMN: Final = 2
# Column of the transcription's language.
LANGUAGE_COLUMN: Final = 3
# Column of the choice (a combo box; empty for a file that needs attention).
CHOICE_COLUMN: Final = 4
# Column of the row's status: changed and not applied, a choice missing, or the file's problem.
STATUS_COLUMN: Final = 5
# Column of what the file holds (its subtitle streams or audio tracks).
DETAILS_COLUMN: Final = 6
