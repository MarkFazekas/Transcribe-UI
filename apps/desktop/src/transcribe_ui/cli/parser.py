"""The command line's argument parser; its help texts are shown in the active language.

Only the help texts the app gives argparse are translated (`tr`): the description and the help
of each command and argument. The names of the arguments stay English (argparse's error messages
quote them, and those become JSON); argparse's own texts (`usage:`,
`options:`, `show this help message and exit`) stay English.
"""

import argparse
import io
import sys
from collections.abc import Sequence
from typing import NoReturn

from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.constants import DISTRIBUTION_NAME
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.language import start_language


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
    """Return the command-line parser, its help texts in the active language."""
    parser = StrictParser(prog=DISTRIBUTION_NAME, description=tr("Run Transcribe-UI requests from JSON files."))
    parser.add_argument("--version", action="store_true", help=tr("print the app version and exit"))
    # The names of the arguments stay English: argparse quotes them in its error messages, which
    # become the message of the `invalid_arguments` JSON response (never translated).
    commands = parser.add_subparsers(dest="command", metavar="command")
    run_help = tr("run the request in a JSON file and print the response as JSON")
    run_parser = commands.add_parser("run", help=run_help, description=run_help)
    run_parser.add_argument("request", help=tr("path of the request JSON file"))
    schema_help = tr("print the JSON Schema of the requests")
    schema_parser = commands.add_parser("schema", help=schema_help, description=schema_help)
    response_help = tr("print the JSON Schema of the responses instead")
    schema_parser.add_argument("--response", action="store_true", help=response_help)
    return parser


def parse_arguments(argv: Sequence[str] | None, language: LanguageSetting) -> argparse.Namespace:
    """Activate the language of the setting (`system`: the OS language), then parse the command line.

    `--help` prints its text and exits (`SystemExit`) here.

    Raises:
        UsageError: The command line is not valid.
    """
    start_language(language)
    # argparse prints `--help` to `sys.stdout`; piped on Windows it would encode in the ANSI code
    # page, which cannot hold every Hungarian text. The JSON goes to the binary buffer, unchanged.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    return build_parser().parse_args(argv)
