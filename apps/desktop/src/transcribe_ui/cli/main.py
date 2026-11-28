"""Command-line frontend: `transcribe-ui run <request.json>`, `schema` and `--version` (§3.1).

The response is printed to stdout as JSON in every case and progress events go to stderr as
NDJSON, both UTF-8 with LF line ends on every OS. Exit codes are in `cli/constants.py`. The JSON
is a machine contract and is never translated; the help texts the app gives argparse are shown
in the language of the `language` setting (argparse's own texts stay English).
"""

import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, NoReturn

from transcribe_ui.api.constants import ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE
from transcribe_ui.api.info import app_version
from transcribe_ui.api.schema import render_schema
from transcribe_ui.api.wiring import build_backend
from transcribe_ui.cli.constants import CLI_LOG_FILE_NAME, ERROR_INVALID_ARGUMENTS
from transcribe_ui.cli.output import failure, respond, write_text
from transcribe_ui.cli.parser import UsageError, parse_arguments
from transcribe_ui.cli.run_command import BackendFactory, cancelled_before_reading, run_request
from transcribe_ui.constants import DISTRIBUTION_NAME
from transcribe_ui.state.logs import log_to_file, user_log_file

logger = logging.getLogger(__name__)


def _dispatch(argv: Sequence[str] | None, make_backend: BackendFactory, out: BinaryIO, err: BinaryIO) -> int:
    # The backend opens nothing yet; it is built first for the language of the help texts.
    backend = make_backend()
    try:
        args = parse_arguments(argv, backend.language_setting())
    except UsageError as error:
        return respond(out, failure(ERROR_INVALID_ARGUMENTS, str(error)))
    if args.version:
        return write_text(out, f"{DISTRIBUTION_NAME} {app_version()}\n")
    if args.command == "schema":
        return write_text(out, render_schema("response" if args.response else "request"))
    if args.command == "run":
        return run_request(Path(args.request), backend, out, err)
    return respond(out, failure(ERROR_INVALID_ARGUMENTS, "A command is required (run or schema)."))


def main(
    argv: Sequence[str] | None = None,
    *,
    make_backend: BackendFactory = build_backend,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
    log_file: Path | None = None,
) -> int:
    """Run the CLI and return the exit code; tests inject the backend factory and the streams.

    Whatever fails, stdout gets a JSON response (`--help` excepted): an unexpected exception is
    answered with `internal_error`, a Ctrl+C outside a request with `cancelled`. Log records and
    warnings go to the rotating `log_file` (dropped when None), never to stderr.
    """
    out = sys.stdout.buffer if stdout is None else stdout
    err = sys.stderr.buffer if stderr is None else stderr
    with log_to_file(log_file):
        try:
            return _dispatch(argv, make_backend, out, err)
        except KeyboardInterrupt:
            return respond(out, cancelled_before_reading())
        except Exception:
            logger.exception("The CLI failed unexpectedly")
            return respond(out, failure(ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE))


def run() -> NoReturn:
    """Entry point of the `transcribe-ui` script."""
    sys.exit(main(log_file=user_log_file(CLI_LOG_FILE_NAME)))
