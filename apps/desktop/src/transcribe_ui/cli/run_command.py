"""`transcribe-ui run <request.json>`: one request from a file, with Ctrl+C as cancel."""

from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse, Request
from transcribe_ui.api.constants import ERROR_INVALID_REQUEST
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.api.schema import parse_request
from transcribe_ui.cli.constants import ERROR_REQUEST_UNREADABLE, NOT_UTF8_MESSAGE, UTF8_BOM, UTF16_BOMS
from transcribe_ui.cli.interrupts import interrupts_cancel
from transcribe_ui.cli.output import failure, respond
from transcribe_ui.cli.progress import NdjsonProgress

# Builds the backend (nothing is opened until a request needs it).
type BackendFactory = Callable[[], Backend]


def read_request(path: Path) -> Request | ErrorResponse:
    """Read and validate the request file (UTF-8, an optional BOM stripped)."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        reason = error.strerror or type(error).__name__
        return failure(ERROR_REQUEST_UNREADABLE, f"Cannot read the request file: {reason}.", {"reason": reason})
    if raw.startswith(UTF16_BOMS):
        return failure(ERROR_INVALID_REQUEST, NOT_UTF8_MESSAGE)
    return parse_request(raw.removeprefix(UTF8_BOM))


def _cancelled(request: Request) -> ErrorResponse:
    return ErrorResponse(action=request.action, error=CancelledError().body())


def run_request(path: Path, backend: Backend, stdout: BinaryIO, stderr: BinaryIO) -> int:
    """Run the request in the file, print the response and return the exit code."""
    request = read_request(path)
    if isinstance(request, ErrorResponse):
        return respond(stdout, request)
    flag = CancelFlag()
    try:
        with interrupts_cancel(flag):
            response = backend.execute(request, NdjsonProgress(stderr), flag)
    except KeyboardInterrupt:
        response = _cancelled(request)
    return respond(stdout, response)


def cancelled_before_reading() -> ErrorResponse:
    """Return the response to a Ctrl+C that arrived before a request was read."""
    return ErrorResponse(action=None, error=CancelledError().body())
