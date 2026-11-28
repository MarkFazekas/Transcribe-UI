"""The commands of the CLI: each takes the parsed command line, the backend and the console."""

import argparse
from collections.abc import Callable, Mapping
from types import MappingProxyType

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.info import app_version
from transcribe_ui.api.schema import render_schema
from transcribe_ui.cli.console import Console
from transcribe_ui.cli.credentials_command import store_typed_credentials
from transcribe_ui.cli.output import write_text
from transcribe_ui.cli.progress import progress_sink
from transcribe_ui.cli.run_command import run_request
from transcribe_ui.constants import DISTRIBUTION_NAME


def print_version(console: Console) -> int:
    """Print the app's name and version (`--version`)."""
    return write_text(console.stdout, f"{DISTRIBUTION_NAME} {app_version()}\n")


# A command: runs with the parsed command line and returns the exit code.
type Command = Callable[[argparse.Namespace, Backend, Console], int]


def _schema(args: argparse.Namespace, _backend: Backend, console: Console) -> int:
    return write_text(console.stdout, render_schema("response" if args.response else "request"))


def _run(args: argparse.Namespace, backend: Backend, console: Console) -> int:
    progress = progress_sink(args.progress, console.stderr)
    return run_request(args.request, backend, console, progress)


def _credentials(_args: argparse.Namespace, backend: Backend, console: Console) -> int:
    return store_typed_credentials(console.active_prompt(), backend, console.stdout)


# The commands by name (`--version` is an option, not a command).
COMMANDS: Mapping[str, Command] = MappingProxyType({"run": _run, "schema": _schema, "credentials": _credentials})
