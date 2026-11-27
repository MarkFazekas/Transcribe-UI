"""Domain errors of the downloads and custom plans; the backend turns them into error responses (§6).

Messages name files by their name only (no folders), like the plan errors, so golden files need
no normalisation of messages.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from transcribe_ui.api.errors import DomainError
from transcribe_ui.placement.classes import PlacementJob, PlanError
from transcribe_ui.placement.constants import (
    ERROR_CUSTOM_PLAN_UNREADABLE,
    ERROR_CUSTOM_PLAN_UNWRITABLE,
    ERROR_DOWNLOAD_PLAN_INVALID,
    ERROR_DOWNLOAD_WRITE_FAILED,
    ERROR_SUBTITLE_MISSING,
)
from transcribe_ui.placement.planner import job_label

if TYPE_CHECKING:
    from pydantic import JsonValue


class DownloadPlanInvalidError(DomainError):
    """The placement plan has errors; nothing was written."""

    def __init__(self, errors: Sequence[PlanError]) -> None:
        """List the plan errors in the details."""
        listed: list[JsonValue] = [error.model_dump(mode="json") for error in errors]
        super().__init__(
            ERROR_DOWNLOAD_PLAN_INVALID,
            "The placement plan has errors; fix them (see details) and send the request again.",
            {"errors": listed},
        )


class DownloadWriteFailedError(DomainError):
    """A subtitle could not be written; the jobs before it are placed and recorded."""

    def __init__(self, job: PlacementJob, error: OSError) -> None:
        """Name the job and the kind of failure (not the OS message: it quotes the path)."""
        super().__init__(
            ERROR_DOWNLOAD_WRITE_FAILED,
            f"{job_label(job)}: the subtitle cannot be written; send the request again once the target is writable.",
            {"item_id": job.item_id, "language": job.language, "reason": type(error).__name__},
        )


class SubtitleMissingError(DomainError):
    """A finished job's subtitle is not in R2; the jobs before it are placed and recorded."""

    def __init__(self, job: PlacementJob) -> None:
        """Name the job."""
        super().__init__(
            ERROR_SUBTITLE_MISSING,
            f"{job_label(job)}: the subtitle is not in the cloud any more.",
            {"item_id": job.item_id, "language": job.language},
        )


class CustomPlanUnreadableError(DomainError):
    """The custom plan file cannot be read (missing, no permission, too large)."""

    def __init__(self, reason: str) -> None:
        """Name the kind of failure (not the OS message: it quotes the path)."""
        super().__init__(ERROR_CUSTOM_PLAN_UNREADABLE, "The custom plan file cannot be read.", {"reason": reason})


class CustomPlanUnwritableError(DomainError):
    """The custom plan file cannot be written."""

    def __init__(self, reason: str) -> None:
        """Name the kind of failure (not the OS message: it quotes the path)."""
        super().__init__(ERROR_CUSTOM_PLAN_UNWRITABLE, "The custom plan file cannot be written.", {"reason": reason})
