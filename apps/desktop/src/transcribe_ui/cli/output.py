"""What the CLI writes to stdout, and the exit code that goes with it."""

from typing import BinaryIO

from transcribe_ui.api.classes import ErrorBody, ErrorResponse, NeedsConfirmation, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import (
    EXIT_CANCELLED,
    EXIT_ERROR,
    EXIT_INVALID_REQUEST,
    EXIT_NEEDS_CONFIRMATION,
    EXIT_OK,
    INVALID_INPUT_CODES,
    LINE_END,
)


def exit_code(response: Response) -> int:
    """Return the process exit code for a response."""
    if isinstance(response, NeedsConfirmation):
        return EXIT_NEEDS_CONFIRMATION
    if not isinstance(response, ErrorResponse):
        return EXIT_OK
    if response.error.code == ERROR_CANCELLED:
        return EXIT_CANCELLED
    if response.error.code in INVALID_INPUT_CODES:
        return EXIT_INVALID_REQUEST
    return EXIT_ERROR


def respond(stdout: BinaryIO, response: Response) -> int:
    """Write the response as indented JSON and return its exit code."""
    stdout.write(response_adapter().dump_json(response, indent=2) + LINE_END)
    stdout.flush()
    return exit_code(response)


def failure(code: str, message: str) -> ErrorResponse:
    """Return an error response raised before any request was read."""
    return ErrorResponse(action=None, error=ErrorBody(code=code, message=message))


def write_text(stdout: BinaryIO, text: str) -> int:
    """Write text as UTF-8 and return the success exit code."""
    stdout.write(text.encode("utf-8"))
    stdout.flush()
    return EXIT_OK
