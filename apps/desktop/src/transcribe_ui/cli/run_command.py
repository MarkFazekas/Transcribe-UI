"""`transcribe-ui run <request.json | ->`: one request from a file or stdin, with Ctrl+C as cancel."""

from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse, Request
from transcribe_ui.api.constants import ERROR_INVALID_REQUEST
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag, ProgressSink
from transcribe_ui.api.schema import parse_request
from transcribe_ui.cli.console import Console
from transcribe_ui.cli.constants import (
    ERROR_REQUEST_UNREADABLE,
    NOT_UTF8_MESSAGE,
    STDIN_ARGUMENT,
    UTF8_BOM,
    UTF16_BOMS,
)
from transcribe_ui.cli.interrupts import interrupts_cancel
from transcribe_ui.cli.output import failure, respond

# Builds the backend (nothing is opened until a request needs it).
type BackendFactory = Callable[[], Backend]


def _unreadable(where: str, error: OSError) -> ErrorResponse:
    reason = error.strerror or type(error).__name__
    return failure(ERROR_REQUEST_UNREADABLE, f"Cannot read {where}: {reason}.", {"reason": reason})


def _parse(raw: bytes) -> Request | ErrorResponse:
    """Validate request bytes: UTF-8, an optional BOM stripped, UTF-16 refused."""
    if raw.startswith(UTF16_BOMS):
        return failure(ERROR_INVALID_REQUEST, NOT_UTF8_MESSAGE)
    return parse_request(raw.removeprefix(UTF8_BOM))


def read_request(path: Path) -> Request | ErrorResponse:
    """Read and validate the request file."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        return _unreadable("the request file", error)
    return _parse(raw)


def read_stdin_request(stdin: BinaryIO) -> Request | ErrorResponse:
    """Read stdin up to its end and validate it as a request (the same rules as a file)."""
    try:
        raw = stdin.read()
    except OSError as error:
        return _unreadable("the request from stdin", error)
    return _parse(raw)


def _read(source: str, stdin: BinaryIO) -> Request | ErrorResponse:
    if source == STDIN_ARGUMENT:
        return read_stdin_request(stdin)
    return read_request(Path(source))


def execute_request(request: Request, backend: Backend, stdout: BinaryIO, progress: ProgressSink | None) -> int:
    """Run a request with Ctrl+C as cancel, print the response and return the exit code (`progress` None: dropped)."""
    flag = CancelFlag()
    try:
        with interrupts_cancel(flag):
            response = backend.execute(request, progress, flag)
    except KeyboardInterrupt:
        response = ErrorResponse(action=request.action, error=CancelledError().body())
    return respond(stdout, response)


def run_request(source: str, backend: Backend, console: Console, progress: ProgressSink) -> int:
    """Run the request of the file (or of stdin when `source` is `-`), print the response, return the exit code."""
    request = _read(source, console.stdin)
    if isinstance(request, ErrorResponse):
        return respond(console.stdout, request)
    return execute_request(request, backend, console.stdout, progress)


def cancelled_before_reading() -> ErrorResponse:
    """Return the response to a Ctrl+C that arrived before a request was read."""
    return ErrorResponse(action=None, error=CancelledError().body())
