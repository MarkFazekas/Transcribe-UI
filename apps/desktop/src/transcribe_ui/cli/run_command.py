"""`transcribe-ui run <request.json>`: one request from a file, with Ctrl+C as cancel."""

import logging
from pathlib import Path
from typing import BinaryIO

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.cli.constants import ERROR_REQUEST_UNREADABLE
from transcribe_ui.cli.interrupts import interrupts_cancel
from transcribe_ui.cli.output import failure, respond
from transcribe_ui.cli.progress import NdjsonProgress
from transcribe_ui.constants import PACKAGE_NAME


def _keep_stderr_for_progress() -> None:
    """Keep log records off stderr, which carries only NDJSON progress (M5.2 adds the log file)."""
    package_logger = logging.getLogger(PACKAGE_NAME)
    if not package_logger.handlers:
        package_logger.addHandler(logging.NullHandler())


def run_request(path: Path, backend: Backend, stdout: BinaryIO, stderr: BinaryIO) -> int:
    """Run the request in the file, print the response and return the exit code."""
    try:
        document = path.read_bytes()
    except OSError as error:
        reason = error.strerror or type(error).__name__
        return respond(stdout, failure(ERROR_REQUEST_UNREADABLE, f"Cannot read the request file: {reason}."))
    _keep_stderr_for_progress()
    flag = CancelFlag()
    try:
        with interrupts_cancel(flag):
            response = backend.execute_json(document, NdjsonProgress(stderr), flag)
    except KeyboardInterrupt:
        response = ErrorResponse(action=None, error=CancelledError().body())
    return respond(stdout, response)
