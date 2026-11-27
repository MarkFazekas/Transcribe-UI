"""Command-line frontend: `transcribe-ui run <request.json>`, `schema` and `--version` (§3.1).

The response is printed to stdout as JSON in every case and progress events go to stderr as
NDJSON, both UTF-8 with LF line ends on every OS. Exit codes are in `cli/constants.py`.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, NoReturn

from transcribe_ui.api.constants import ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE
from transcribe_ui.api.info import app_version
from transcribe_ui.api.schema import render_schema
from transcribe_ui.api.wiring import build_backend
from transcribe_ui.cli.constants import ERROR_INVALID_ARGUMENTS
from transcribe_ui.cli.output import failure, respond, write_text
from transcribe_ui.cli.quiet import logs_off_stderr
from transcribe_ui.cli.run_command import BackendFactory, cancelled_before_reading, run_request
from transcribe_ui.constants import DISTRIBUTION_NAME

logger = logging.getLogger(__name__)


class UsageError(Exception):
    """The command line is not valid."""


class StrictParser(argparse.ArgumentParser):
    """Argument parser that raises instead of printing usage and exiting."""

    def error(self, message: str) -> NoReturn:
        """Raise the problem, so it is answered with an error response.

        Raises:
            UsageError: Always.
        """
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = StrictParser(prog=DISTRIBUTION_NAME, description="Run Transcribe-UI requests from JSON files.")
    parser.add_argument("--version", action="store_true", help="print the app version and exit")
    commands = parser.add_subparsers(dest="command", metavar="command")
    run_parser = commands.add_parser("run", help="run the request in a JSON file and print the response as JSON")
    run_parser.add_argument("request", help="path of the request JSON file")
    schema_parser = commands.add_parser("schema", help="print the JSON Schema of the requests")
    schema_parser.add_argument("--response", action="store_true", help="print the JSON Schema of the responses instead")
    return parser


def _dispatch(argv: Sequence[str] | None, make_backend: BackendFactory, out: BinaryIO, err: BinaryIO) -> int:
    try:
        args = build_parser().parse_args(argv)
    except UsageError as error:
        return respond(out, failure(ERROR_INVALID_ARGUMENTS, str(error)))
    if args.version:
        return write_text(out, f"{DISTRIBUTION_NAME} {app_version()}\n")
    if args.command == "schema":
        return write_text(out, render_schema("response" if args.response else "request"))
    if args.command == "run":
        return run_request(Path(args.request), make_backend, out, err)
    return respond(out, failure(ERROR_INVALID_ARGUMENTS, "A command is required (run or schema)."))


def main(
    argv: Sequence[str] | None = None,
    *,
    make_backend: BackendFactory = build_backend,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> int:
    """Run the CLI and return the exit code; tests inject the backend factory and the streams.

    Whatever fails, stdout gets a JSON response (`--help` excepted): an unexpected exception is
    answered with `internal_error`, a Ctrl+C outside a request with `cancelled`.
    """
    out = sys.stdout.buffer if stdout is None else stdout
    err = sys.stderr.buffer if stderr is None else stderr
    with logs_off_stderr():
        try:
            return _dispatch(argv, make_backend, out, err)
        except KeyboardInterrupt:
            return respond(out, cancelled_before_reading())
        except Exception:
            logger.exception("The CLI failed unexpectedly")
            return respond(out, failure(ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE))


def run() -> NoReturn:
    """Entry point of the `transcribe-ui` script."""
    sys.exit(main())
