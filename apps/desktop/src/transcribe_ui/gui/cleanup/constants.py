"""Constants of the retry and cloud cleanup handlers (architecture §3.1 "GUI")."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from transcribe_ui.batches.payloads import CleanupScope
from transcribe_ui.cleanup.constants import CONFIRMATION_NOT_DOWNLOADED

# The cleanup scope a `clean_up` signal names, by its text (any other text is not a scope).
SCOPES: Final[Mapping[str, CleanupScope]] = MappingProxyType({"audio": "audio", "everything": "everything"})
# The scope of every cleanup of another computer's job (Q28: only everything).
UNKNOWN_SCOPE: Final[CleanupScope] = "everything"
# First part of the activity key of a job's retry or cleanup (one cloud step per job at a time).
ACTIVITY_KEY: Final = "cleanup"
# Confirmation code shown as a warning (the subtitles may not be downloaded yet).
WARNING_CONFIRMATIONS: Final = frozenset((CONFIRMATION_NOT_DOWNLOADED,))
# Details key of `retry_copying` that names the retry job.
RETRY_BATCH_KEY: Final = "retry_batch_id"
# Bus signal: open a job page (`batch_id`).
OPEN_JOB_SIGNAL: Final = "open_job"
# Bus signal: the launch plan changed (another computer's job deleted); the start dialog plans again.
PLAN_INVALIDATED_SIGNAL: Final = "plan_invalidated"
# Bus signals that take the user elsewhere: an answer that arrives after one asks nothing and opens nothing.
NAVIGATION_SIGNALS: Final = (
    "open_job",
    "open_jobs",
    "open_new_job",
    "open_cloud",
    "open_cloud_machines",
    "open_settings",
    "edit_selection",
    "open_review",
    "open_download",
)
