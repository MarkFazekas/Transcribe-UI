"""Domain errors: raised by request handlers, turned into `ErrorResponse`s by the backend (§6)."""

from collections.abc import Mapping

from pydantic import JsonValue

from transcribe_ui.api.classes import ErrorBody
from transcribe_ui.api.constants import ERROR_CANCELLED

# Structured details of an error.
type Details = Mapping[str, JsonValue]


class DomainError(Exception):
    """An expected failure with a stable code; the frontends show its message."""

    def __init__(self, code: str, message: str, details: Details | None = None) -> None:
        """Keep the code, the message and the structured details."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def body(self) -> ErrorBody:
        """Return the error as the body of an `ErrorResponse`."""
        return ErrorBody(code=self.code, message=self.message, details=self.details)


class CancelledError(DomainError):
    """The request was cancelled; what was done is persisted and re-sending resumes."""

    def __init__(self) -> None:
        """Use the fixed `cancelled` code and message."""
        super().__init__(ERROR_CANCELLED, "Cancelled. Work done so far is saved; send the request again to resume.")
