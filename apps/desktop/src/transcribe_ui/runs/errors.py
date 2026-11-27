"""Errors of the run-activity check."""

from typing import Literal

from transcribe_ui.api.errors import DomainError
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE

# Which part of the cloud could not be read.
type CloudPart = Literal["runpod", "r2"]


class ActivityUnknownError(DomainError):
    """The Runpod lists or R2 could not be read, so a run may be active: the check fails closed."""

    def __init__(self, part: CloudPart, cause: str) -> None:
        """Name the part and the cause (an error code, never request data) in the details."""
        super().__init__(
            ERROR_CLOUD_UNREACHABLE,
            "Whether a worker run is active cannot be checked, so nothing was started or published; try again later.",
            {"part": part, "cause": cause},
        )
