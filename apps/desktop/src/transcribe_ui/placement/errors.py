"""Domain errors of the downloads and custom plans; the backend turns them into error responses (§6)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from transcribe_ui.api.errors import DomainError
from transcribe_ui.placement.classes import PlanError
from transcribe_ui.placement.constants import (
    ERROR_CUSTOM_PLAN_UNREADABLE,
    ERROR_CUSTOM_PLAN_UNWRITABLE,
    ERROR_DOWNLOAD_PLAN_INVALID,
)

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
