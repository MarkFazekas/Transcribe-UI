"""Constants of the retry and cloud cleanup handlers (architecture §3.1 "GUI")."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from transcribe_ui.batches.payloads import CleanupScope
from transcribe_ui.cleanup.constants import (
    CONFIRMATION_CLEANUP_AUDIO,
    CONFIRMATION_CLEANUP_ELSEWHERE,
    CONFIRMATION_CLEANUP_EVERYTHING,
    CONFIRMATION_NOT_DOWNLOADED,
    CONFIRMATION_UNKNOWN_BATCH,
    CONFIRMATION_UNKNOWN_ELSEWHERE,
)

# What a cleanup deletes: a job's audio, all of a job's cloud data, or another computer's job.
type CleanupKind = Literal["audio", "everything", "unknown"]

# The cleanup scope a `clean_up` signal names, by its text (any other text is not a scope).
SCOPES: Final[Mapping[str, CleanupScope]] = MappingProxyType({"audio": "audio", "everything": "everything"})
# The scope of every cleanup of another computer's job (Q28: only everything).
UNKNOWN_SCOPE: Final[CleanupScope] = "everything"
# What a question that deletes all of a job's cloud data deletes (both forms of the question).
EVERYTHING_KIND: Final[CleanupKind] = "everything"
# What a question about another computer's job deletes (both forms of the question).
UNKNOWN_KIND: Final[CleanupKind] = "unknown"
# First part of the activity key of a job's retry or cleanup (one cloud step per job at a time).
ACTIVITY_KEY: Final = "cleanup"
# Confirmation codes shown as a warning: the subtitles may not be downloaded yet, or another
# computer uploaded the job and may still need them (architecture §3.9).
WARNING_CONFIRMATIONS: Final = frozenset(
    (CONFIRMATION_NOT_DOWNLOADED, CONFIRMATION_CLEANUP_ELSEWHERE, CONFIRMATION_UNKNOWN_ELSEWHERE),
)
# What the question deletes, by the confirmation code that says so (the first one found wins).
CONFIRMATION_KINDS: Final[Mapping[str, CleanupKind]] = MappingProxyType(
    {
        CONFIRMATION_UNKNOWN_BATCH: UNKNOWN_KIND,
        CONFIRMATION_UNKNOWN_ELSEWHERE: UNKNOWN_KIND,
        CONFIRMATION_CLEANUP_EVERYTHING: EVERYTHING_KIND,
        CONFIRMATION_CLEANUP_ELSEWHERE: EVERYTHING_KIND,
        CONFIRMATION_CLEANUP_AUDIO: "audio",
    },
)
# Style of the cleanup question's heading and of its warning lines.
EMPHASIS_STYLE: Final = "font-weight: bold;"
# Details key of `retry_copying` that names the retry job.
RETRY_BATCH_KEY: Final = "retry_batch_id"
# Bus signal: open a job page (`batch_id`).
OPEN_JOB_SIGNAL: Final = "open_job"
# Bus signal: the launch plan changed (another computer's job deleted); the start dialog plans again.
PLAN_INVALIDATED_SIGNAL: Final = "plan_invalidated"
