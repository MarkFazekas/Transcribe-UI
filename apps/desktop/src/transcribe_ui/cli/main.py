"""Command-line frontend: `transcribe-ui run <request.json | ->`, `schema`, `credentials`, `--version` (§3.1).

The response is printed to stdout as JSON in every case and progress events go to stderr as
NDJSON (`run --progress text`: readable lines), both UTF-8 with LF line ends on every OS. Exit codes are in `cli/constants.py`. The JSON
is a machine contract and is never translated; the help texts the app gives argparse are shown
in the language of the `language` setting (argparse's own texts stay English).
"""

import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from transcribe_ui.api.constants import ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE
from transcribe_ui.api.wiring import build_backend
from transcribe_ui.cli.commands import COMMANDS, print_version
from transcribe_ui.cli.console import Console
from transcribe_ui.cli.constants import CLI_LOG_FILE_NAME, ERROR_INVALID_ARGUMENTS, NO_COMMAND_MESSAGE
from transcribe_ui.cli.output import failure, respond
from transcribe_ui.cli.parser import UsageError, parse_arguments
from transcribe_ui.cli.run_command import BackendFactory, cancelled_before_reading
from transcribe_ui.state.logs import log_to_file, user_log_file

logger = logging.getLogger(__name__)


def _dispatch(argv: Sequence[str] | None, make_backend: BackendFactory, console: Console) -> int:
    # The backend opens nothing yet; it is built first for the language of the help texts.
    backend = make_backend()
    try:
        args = parse_arguments(argv, backend.language_setting())
    except UsageError as error:
        return respond(console.stdout, failure(ERROR_INVALID_ARGUMENTS, str(error)))
    if args.version:
        return print_version(console)
    command = COMMANDS.get(args.command)
    if command is None:
        return respond(console.stdout, failure(ERROR_INVALID_ARGUMENTS, NO_COMMAND_MESSAGE))
    return command(args, backend, console)


def main(
    argv: Sequence[str] | None = None,
    *,
    make_backend: BackendFactory = build_backend,
    console: Console | None = None,
    log_file: Path | None = None,
) -> int:
    """Run the CLI and return the exit code; tests inject the backend factory and the console.

    Whatever fails, stdout gets a JSON response (`--help` excepted): an unexpected exception is
    answered with `internal_error`, a Ctrl+C outside a request with `cancelled`. Log records and
    warnings go to the rotating `log_file` (dropped when None), never to stderr.
    """
    streams = Console() if console is None else console
    with log_to_file(log_file):
        try:
            return _dispatch(argv, make_backend, streams)
        except KeyboardInterrupt:
            return respond(streams.stdout, cancelled_before_reading())
        except Exception:
            logger.exception("The CLI failed unexpectedly")
            return respond(streams.stdout, failure(ERROR_INTERNAL, INTERNAL_ERROR_MESSAGE))


def run() -> NoReturn:
    """Entry point of the `transcribe-ui` script."""
    sys.exit(main(log_file=user_log_file(CLI_LOG_FILE_NAME)))
