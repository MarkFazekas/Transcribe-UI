"""Entry point of the GUI executable, `transcribe-ui-gui` (A20: a windowed executable of its own).

This module is Qt-free: it checks for a display first (Qt's failure to load a platform plugin
aborts the process, which cannot be answered) and only then imports the GUI. Without a display
it logs why, answers like the CLI (a JSON `ErrorResponse` with the code `display_unavailable`
on stdout, when there is one) and exits with 1. The command line is the separate `transcribe-ui`
executable, which never imports Qt.
"""

import importlib
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, NoReturn

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.constants import ERROR_DISPLAY_UNAVAILABLE
from transcribe_ui.api.wiring import build_backend
from transcribe_ui.cli.constants import EXIT_ERROR
from transcribe_ui.cli.output import failure, respond
from transcribe_ui.gui.constants import DISPLAY_UNAVAILABLE_MESSAGE, GUI_APP_MODULE, GUI_LOG_FILE_NAME
from transcribe_ui.gui.display import display_available
from transcribe_ui.state.logs import log_to_file, user_log_file

logger = logging.getLogger(__name__)

# Builds the backend of the GUI (tests: seeded ports).
type BackendFactory = Callable[[], Backend]
# Runs the GUI over a backend factory until its window closes; returns the exit code.
type GuiRunner = Callable[[BackendFactory], int]


def _standard_output() -> BinaryIO | None:
    # A windowed executable on Windows has no stdout (None); started from a terminal elsewhere it has one.
    stream = sys.stdout
    return None if stream is None else stream.buffer


def main(
    make_backend: BackendFactory = build_backend,
    log_file: Path | None = None,
    stdout: BinaryIO | None = None,
) -> int:
    """Run the GUI, logging to `log_file` (dropped when None); return the exit code.

    Without a display the `display_unavailable` response goes to `stdout` (the process's unless
    given) and the exit code is 1.
    """
    with log_to_file(log_file):
        if not display_available():
            logger.error(DISPLAY_UNAVAILABLE_MESSAGE)
            response = failure(ERROR_DISPLAY_UNAVAILABLE, DISPLAY_UNAVAILABLE_MESSAGE)
            out = _standard_output() if stdout is None else stdout
            if out is None:
                return EXIT_ERROR
            return respond(out, response)
        module = importlib.import_module(GUI_APP_MODULE)
        run_gui: GuiRunner = module.run_gui
        return run_gui(make_backend)


def run() -> NoReturn:
    """Entry point of the `transcribe-ui-gui` script."""
    sys.exit(main(log_file=user_log_file(GUI_LOG_FILE_NAME)))
