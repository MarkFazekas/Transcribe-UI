"""Constants of the jobs list and the job page (architecture §3.1 "GUI")."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

# A button of the job page; the allowed actions (`BatchSummary.actions`) decide which ones show.
type JobButtonKey = Literal[
    "edit_selection",
    "continue_review",
    "delete",
    "review",
    "upload",
    "download",
    "retry",
    "cleanup_audio",
    "cleanup_everything",
    "hide",
    "show",
]

# The button of the review step, which serves `review_batch` and `resolve_review`.
REVIEW_BUTTON: Final[JobButtonKey] = "review"
# The button of the download dialog, which serves every download action.
DOWNLOAD_BUTTON: Final[JobButtonKey] = "download"
# The button of each allowed action, in the order of the actions. Several actions can share one
# button (the review and the downloads); `exclude_items` has none here (the review page offers it).
ACTION_BUTTONS: Final[Mapping[str, JobButtonKey]] = MappingProxyType(
    {
        "edit_batch": "edit_selection",
        "start_review": "continue_review",
        "delete_batch": "delete",
        "review_batch": REVIEW_BUTTON,
        "resolve_review": REVIEW_BUTTON,
        "run_upload": "upload",
        "plan_download": DOWNLOAD_BUTTON,
        "run_download": DOWNLOAD_BUTTON,
        "export_custom_plan": DOWNLOAD_BUTTON,
        "import_custom_plan": DOWNLOAD_BUTTON,
        "create_retry_batch": "retry",
        "cleanup_audio": "cleanup_audio",
        "cleanup_everything": "cleanup_everything",
        "hide_batch": "hide",
        "show_batch": "show",
    },
)
# Buttons that only emit their bus signal (`gui/signals.py`) with the batch id, and that signal's name.
SIGNAL_BUTTONS: Final[Mapping[JobButtonKey, str]] = MappingProxyType(
    {
        "edit_selection": "edit_selection",
        REVIEW_BUTTON: "open_review",
        "upload": "run_upload",
        DOWNLOAD_BUTTON: "open_download",
    },
)
# Cleanup buttons and the scope their `clean_up` signal carries.
CLEANUP_BUTTONS: Final[Mapping[JobButtonKey, str]] = MappingProxyType(
    {"cleanup_audio": "audio", "cleanup_everything": "everything"},
)
# Buttons the jobs list offers for the selected job.
LIST_BUTTONS: Final[frozenset[JobButtonKey]] = frozenset(("hide", "show"))
# Signal that retries jobs (`batch_id`, the ticked jobs as a tuple of `RetryJob`).
RETRY_SIGNAL: Final = "retry_jobs"
# Signal that cleans up a batch's cloud data (`batch_id`, the scope).
CLEAN_UP_SIGNAL: Final = "clean_up"
# Signal that opens a batch's review step (`batch_id`).
OPEN_REVIEW_SIGNAL: Final = "open_review"
# Signal that opens a job page (`batch_id`).
OPEN_JOB_SIGNAL: Final = "open_job"
# Signal that opens the jobs list.
OPEN_JOBS_SIGNAL: Final = "open_jobs"
# Channel key of the jobs list's loads (a new load replaces the previous one).
LIST_KEY: Final = "jobs_list"
# Channel key of the job page's loads.
PAGE_KEY: Final = "job_page"
# Separator of the languages and the labels in one cell.
CELL_SEPARATOR: Final = ", "
